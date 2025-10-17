# ThingsBoard Telemetry Stress Kit

This project provisions up to **500** simulated devices in ThingsBoard, drives MQTT telemetry in synchronized bursts to stress the server, and exposes a detailed dashboard highlighting throughput, bandwidth and failure causes in real time.

---

## Contents

1. [Requirements](#requirements)  
2. [Environment](#environment)  
3. [Installation](#installation)  
4. [Running the simulation](#running-the-simulation)  
5. [Live metrics and dashboard](#live-metrics-and-dashboard)  
6. [Cleaning up devices](#cleaning-up-devices)  
7. [Connectivity check](#connectivity-check)  
8. [Troubleshooting](#troubleshooting)

---

## Requirements

- Python 3.9+ (virtual environment recommended).  
- ThingsBoard tenant credentials (`TB_URL`, `TB_USERNAME`, `TB_PASSWORD`).  
- MQTT endpoint reachability (`MQTT_HOST`, `MQTT_PORT`, optional TLS).  
- Docker is optional if you prefer containerised execution.

---

## Environment

Populate `.env` with your tenant and broker details. The simulation constrains the fleet to 500 devices to focus on high-load experiments.

| Variable | Description |
|----------|-------------|
| `TB_URL` | Base REST URL for ThingsBoard (no trailing slash). |
| `TB_USERNAME`, `TB_PASSWORD` | Tenant credentials. |
| `TB_TIMEOUT` | REST timeout (seconds, default 60). |
| `DEVICE_PREFIX` | Base name for simulated devices (default `sim`). |
| `DEVICE_COUNT` | Number of devices to provision/stream (0 < count ≤ 500). |
| `KEEP_DEVICE_COUNT` | Optional override for the cleanup script (defaults to `DEVICE_COUNT`). |
| `DEVICE_LABEL`, `DEVICE_TYPE` | Metadata applied to each device. |
| `DEVICE_PROFILE_ID` | Optional profile id; falls back to the tenant default. |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS` | MQTT connection settings. |
| `PUBLISH_INTERVAL_SEC` | Interval between bursts per device. All threads share the exact cadence. |
| `SIM_START_LEAD_TIME` | Lead time (seconds) before the first synchronized burst (default 0.3). |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | Flask dashboard bind address (default `0.0.0.0:5000`). |
| `DASHBOARD_REFRESH_MS` | Dashboard polling interval (milliseconds). |
| `PROVISION_RETRIES`, `PROVISION_RETRY_DELAY` | Backoff parameters for device provisioning. |

> Update `.env` and rerun the simulator whenever you adjust credentials, the device cap, or MQTT parameters.

---

## Installation

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Running the simulation

1. **Configure** `.env` with the desired device count (≤ 500) and broker/TB credentials.  
2. **Launch** the simulator from an activated virtual environment:
   ```powershell
   .\.venv\Scripts\python.exe scripts/create_and_stream.py
   ```
   The script will:
   - Provision or reuse `DEVICE_COUNT` devices (tokens saved to `data/tokens.json`).
   - Start one MQTT client thread per device.
   - Synchronize every send operation so that all devices publish at the exact same time slice.
   - Start a Flask dashboard at `http://<DASHBOARD_HOST>:<DASHBOARD_PORT>`.
   - Emit `[METRICS]` lines every five seconds summarizing health and throughput.
3. **Stop** with `Ctrl+C`. The simulator shuts down the dashboard, waits for all worker threads, and prints a final report.

Whenever you change the profile or device count, simply edit `.env` and run the script again. Devices are reused where possible; missing ones are created automatically.

---

## Live metrics and dashboard

`scripts/create_and_stream.py` exposes `/metrics` for the dashboard and logs. The collector keeps track of:

- Connected, disconnected and failed device counts.  
- Total packets sent/failed, average rate per device, and total data volume (MB).  
- Estimated bandwidth usage (Mbps) and number of active MQTT channels.  
- Time-to-collapse (seconds until the last device disconnects while the run is active).  
- Detailed failure breakdowns (authentication, network, TLS, payload, memory, etc.) including stage and MQTT codes.

The dashboard (Chart.js + HTML) displays:

- A timeline of connected vs failed/disconnected devices.  
- Metric tiles with throughput, bandwidth, collapse timers and burst interval.  
- A failure table aggregating root causes.  
- A device health table (top 40 problematic devices) including last telemetry timestamp, failure reason, and stage.

All telemetry threads share a lead time (`SIM_START_LEAD_TIME`) and deterministic cadence, guaranteeing simultaneous bursts and consistent server pressure.

---

## Cleaning up devices

To trim devices beyond the configured cap without touching the healthy fleet, run:

```powershell
.\.venv\Scripts\python.exe scripts/delete_by_prefix.py
```

The script keeps the first `KEEP_DEVICE_COUNT` (defaults to `DEVICE_COUNT`) devices matching `DEVICE_PREFIX` and deletes the rest. Use this after testing to reset the tenant to the 500-device baseline.

`data/tokens.json` and `data/devices.csv` ship empty; they are repopulated on each simulator run.

---

## Connectivity check

Use `scripts/check_connectivity.py` for a quick ThingsBoard sanity check:

```powershell
.\.venv\Scripts\python.exe scripts/check_connectivity.py
```

It validates the login credentials and confirms the REST API is reachable.

---

## Troubleshooting

- **MQTT connection errors (`rc != 0`)**: check broker availability, credentials, or TLS settings. The dashboard will classify the error (auth, network, payload, etc.).  
- **Provisioning backoff exhausted**: increase `PROVISION_RETRIES` or `PROVISION_RETRY_DELAY` if ThingsBoard throttles requests.  
- **Dashboard unreachable**: verify the port is open or adjust `DASHBOARD_PORT`. You can always hit `/metrics` directly.  
- **Collapse timer triggers early**: inspect the failure table to see which stage (connect, publish, disconnect) is responsible and adjust broker/server tuning accordingly.  
- **Need to restart cleanly**: stop the simulation with `Ctrl+C`, run `scripts/delete_by_prefix.py` to trim excess devices, then start again.

With these tools you can generate synchronized load, observe bandwidth and failure causes in real time, and iterate on server hardening with a consistent 500-device fleet.
