# ThingsBoard Telemetry Load Lab

## 1. What This Project Does
This repository creates a realistic, repeatable workload for a ThingsBoard tenant. It:
- Provisions (or reuses) a fleet of 100 simulated devices tied to the Device Profile `3a022cf0-aae1-11f0-bea7-7bc7d3c79da2`.
- Opens simultaneous MQTT connections (one per device) and publishes telemetry in parallel.
- Logs every message, disconnect, and error for post-run analysis.
- Exposes a real-time metrics dashboard (Flask on port 5050 by default) showing throughput, bandwidth, connections, and failure causes.
- Generates structured JSON reports so you can track capacity changes between runs.

> **Goal:** give engineers full control to stress ThingsBoard, understand its limits, and triage bottlenecks quickly.

---

## 2. System Requirements
| Requirement          | Description                                                                                     |
|----------------------|-------------------------------------------------------------------------------------------------|
| OS                   | Windows, macOS, or Linux (scripts are cross-platform).                                          |
| Python               | Version 3.9 or newer. On Windows, use the `py` launcher.                                        |
| Network              | Reachable ThingsBoard REST endpoint and MQTT broker (plain TCP or TLS).                         |
| Credentials          | Tenant-level username/password for provisioning devices.                                        |
| Optional tools       | Docker (not required), VS Code (tasks/launch configs included).                                |

Install dependencies once per machine:
```bash
py -3 -m pip install -r requirements.txt
```

---

## 3. Repository Layout
```
.venv/                      # Recommended virtual environment (ignored in Git)
.vscode/                    # Ready-to-use VS Code launch configurations and tasks
data/
  provisioning/             # tokens.json + devices.csv generated during provisioning
  runs/                     # run reports (JSON) including latest.json symlink/copy
  logs/                     # per-run execution logs
  control/                  # pid + toggle files used to manage manual overrides
scripts/
  mqtt/                     # MQTT tooling (simulators, provisioning, helpers)
    mqtt_stress_async.py    # orchestrator + async workers + global dashboard
    run_stress_suite.py     # automatiza provisión, activación y ejecución de la prueba
    metrics_server.py       # aggregated metrics server
    create_devices.py       # provisioning helper
    delete_devices.py       # cleanup helpers
    activate_devices.py     # activa dispositivos registrados
    deactivate_devices.py   # desactiva dispositivos registrados
    toggle_devices.py       # manual activation/deactivation helper
    stop_simulation.py      # sends signals to stop the async simulator
    send_telemetry.py       # legacy threaded simulator
    tb.py                   # shared ThingsBoard REST client
    ...
requirements.txt
.env                        # local-only configuration (never commit secrets)
```
Everything under `data/` is ignored by Git so tokens and logs stay local. All MQTT-related scripts now reside inside `scripts/mqtt/`.

---

## 4. Configuration (`.env`)
Create or edit `.env` in the repo root. Each entry is read by both provisioning and telemetry scripts.

| Key                     | Purpose                                                                                   |
|-------------------------|--------------------------------------------------------------------------------------------|
| `TB_URL`                | Base URL to the ThingsBoard REST API (e.g., `http://IP:8080`).                             |
| `TB_PARENT_URL`         | Optional parent/central ThingsBoard URL for mirrored cleanup (e.g., `http://192.168.1.159:8080`). |
| `TB_USERNAME` / `TB_PASSWORD` | Tenant credentials used for device provisioning.                                  |
| `TB_PARENT_USERNAME` / `TB_PARENT_PASSWORD` | Override credentials for the parent server (defaults to the edge values). |
| `DEVICE_PREFIX`         | Name prefix for simulated devices (default `sim`).                                         |
| `DEVICE_COUNT`          | Number of devices to maintain (default 100).                                               |
| `DEVICE_LABEL` / `DEVICE_TYPE` | Metadata applied to every device.                                                  |
| `MQTT_HOST` / `MQTT_PORT` / `MQTT_TLS` | MQTT broker settings (`MQTT_TLS=1` forces TLS).                          |
| `PUBLISH_INTERVAL_SEC`  | Seconds between telemetry messages for each device.                                        |
| `DEVICE_PROFILE_ID`     | Profile ID used when provisioning; dashboard counts only devices on this profile.          |
| `METRICS_HOST` / `METRICS_PORT` / `METRICS_REFRESH_MS` | Controls the Flask dashboard binding and refresh cadence. |
| `DISABLED_DEVICES_FILE` | Optional override for the manual disable list consumed by the simulator.                   |
| `SIM_PID_FILE`          | Optional override for the PID file used by `stop_simulation.py`.                            |

> **Tip:** keep `.env` out of Git (already covered by `.gitignore`). Rotate credentials periodically.

---

## 5. End-to-End Workflow

### Step 1 - Optional Connectivity Check
Validates basic REST access before provisioning.
```bash
py -3 scripts/mqtt/check_connectivity.py
```
- Performs a login, prints `[OK] Login exitoso`, and probes the devices list.
- Failure here usually means wrong `TB_URL` or credentials.

### Step 2 - Provision or Update Devices
```bash
py -3 "scripts/mqtt/create_devices.py"
```
What happens:
1. Logs into ThingsBoard, discovers the default profile (or uses `DEVICE_PROFILE_ID`).
2. Ensures `DEVICE_COUNT` devices named `<DEVICE_PREFIX>-NNN` exist.
3. Writes access tokens to `data/provisioning/tokens.json`.
4. Exports device metadata to `data/provisioning/devices.csv`.
5. Stores context attributes (`batch`, `group`, `index`) for easier filtering inside ThingsBoard.

Rerun whenever you change `DEVICE_COUNT`, `DEVICE_PREFIX`, or want to rotate tokens.

### Step 3 - Start the Telemetry Simulation
```bash
py -3 "scripts/mqtt/mqtt_stress_async.py"
```
This single command:
1. Reads `tokens.json`, splits the fleet into shards (max `--max-clients-per-process` each), and launches async workers per shard.
2. Connects every client to MQTT, posts snapshots to the global aggregator, and keeps telemetry flowing every `PUBLISH_INTERVAL_SEC` seconds.
3. Starts a single Flask dashboard (default `http://localhost:5050`) that already shows the aggregated totals from all shards.
4. Logs events per device to `data/logs/<run-id>-sXXXXX-nXXXXX-events.jsonl`.
5. Streams metrics snapshots to `data/metrics/<run-id>-sXXXXX-nXXXXX-metrics.csv` for each shard.
6. Writes a consolidated JSON report under `data/runs/<run-id>.json` (and refreshes `data/runs/latest.json`).

> **Atajo:** `py -3 scripts/mqtt/run_stress_suite.py --deactivate-after` ejecuta la provision (si procede), activa la flota, lanza el simulador y desactiva todo al terminar. Anade tus parametros extra tras `--` para reenviarlos al simulador.
Stop the simulation with `Ctrl+C`. The threads shut down gracefully, the metrics server is closed, and **devices remain in ThingsBoard** for subsequent runs.

#### Manual Overrides
- Detén una ejecución en curso sin acceder a la consola original: `py -3 scripts/mqtt/stop_simulation.py`. El script lee `data/control/mqtt_stress.pid`, envía `SIGTERM` y limpia el PID si el proceso finaliza.
- Activa toda la flota antes de una prueba: `py -3 scripts/mqtt/activate_devices.py --all`.
- Desactiva todo al terminar para que nada quede enviando telemetría: `py -3 scripts/mqtt/deactivate_devices.py --all`.
- Para operaciones selectivas (por nombre o prefijo) sigue disponible `py -3 scripts/mqtt/toggle_devices.py`, que sincroniza `data/control/disabled_devices.json` y actualiza los atributos `manual_*` en ThingsBoard. Añade `--dry-run` para validar cambios sin aplicarlos.

#### Docker Execution
1. Construye la imagen: `docker build -t tb-load-lab .`
2. Ejecuta una prueba completa montando la carpeta `data/` para conservar tokens, logs y reportes:
   - Linux/macOS:
     ```bash
     docker run --rm --env-file .env -v "$(pwd)/data:/app/data" tb-load-lab --deactivate-after --duration 120 --device-count 20
     ```
   - Windows PowerShell:
     ```powershell
     docker run --rm --env-file .env -v "${PWD}\data:/app/data" tb-load-lab --deactivate-after --duration 120 --device-count 20
     ```
3. Para invocar otro script usa el mismo contenedor:
   `docker run --rm --env-file .env -v "$(pwd)/data:/app/data" tb-load-lab python -m scripts.mqtt.deactivate_devices --all`
> La imagen usa `python -m scripts.mqtt.run_stress_suite` como entrypoint, por lo que cualquier argumento tras el nombre de la imagen se envia al orquestador.

---

## 6. Telemetry Payload & Device Tracking
Each MQTT message contains:
```json
{
  "timestamp": "2025-10-17T20:15:32.123Z",
  "device": "sim-042",
  "sequence": 128,
  "temperature": 26.45,
  "humidity": 55,
  "battery": 3.81,
  "cpu_usage_percent": 43.2,
  "memory_usage_mb": 210.5,
  "network_latency_ms": 82.7,
  "status": "warn",
  "issue": "network-latency"
}
```

Per device, the simulator records:
- Connection time, first publish, last publish.
- Message counters (success/failure).
- Disconnects with MQTT reason codes (`connection lost`, `protocol violation`, etc.).
- Runtime exceptions with coarse root cause classification (`network`, `memory`, generic).

These metrics are stored in memory during the run, surfaced via the dashboard, and persisted in `data/runs/<run-id>.json`.

---

## 7. Real-Time Metrics Dashboard (Flask)
When the simulation starts, browse to the metrics server:
```
http://<METRICS_HOST>:<METRICS_PORT>   # default http://localhost:5050
```

### Dashboard Sections
- **Status Bar** - elapsed time, messages per second, current bandwidth (Mbps), channels in use (current connections).
- **Connections Card** - total devices (from `.env`), current connected, peak connected, failed device count, collapse detection (time and reason).
- **Traffic Card** - packets sent/failed, cumulative volume in MB, average messages per device, average message rate per device.
- **Disconnect Causes Table** - top 10 reasons (aggregated from MQTT return codes and error handlers).
- **Messages per Second Chart** - rolling line chart (last ~60 points).
- **Bandwidth Chart** - Mbps trend, useful to spot saturation.
- **Top Devices Table** - devices ordered by sent messages, showing failed counts per device.

### Back-End Architecture
- `MetricsCollector` (in `scripts/mqtt/send_telemetry.py`) aggregates metrics under thread locks.
- `MetricsServer` (in `scripts/mqtt/metrics_server.py`) exposes the `/api/metrics` endpoint consumed by the dashboard and any external tooling.
- Data refresh rate is controlled by `METRICS_REFRESH_MS` (default 2000 ms).

---

## 8. Post-Run Analysis

### Logs
- `data/logs/<run-id>.log` - chronological events (connections, disconnections, publish results, metrics snapshots).
  - Recommended filters: `"[ERR]"`, `"[WARN]"`, `"[MQTT]"`, `"Metrics |"`.

### JSON Report
- `data/runs/<run-id>.json` - structured summary used by scripts and dashboards.
  - `metrics` object mirrors dashboard values (messages/sec, bandwidth, collapse detection, etc.).
  - `devices` dictionary holds per-device metrics (timestamps, payload, disconnect history).
- `data/runs/latest.json` is updated after every run for easy access.

### CLI Summary
```bash
py -3 scripts/report_last_run.py
```
Outputs:
- Global metrics (connections, bandwidth, messages/sec, averages, disconnect causes).
- Devices with errors, stalled telemetry, or delayed starts.
- First 20 devices with sent message counts and disconnect totals.

Use this as a quick sanity check or to paste results into incident reports.

---

## 9. Script Reference
| Script | Description | Typical Usage |
|--------|-------------|----------------|
| `scripts/mqtt/run_stress_suite.py` | Orquesta provisión, activación y simulación en un solo comando. | Ejecuta pruebas de estrés sin lanzar scripts manualmente. |
| `scripts/mqtt/create_devices.py` | Provisions/updates the simulated fleet; saves tokens and CSV. | Run whenever the device fleet needs to change or tokens should refresh. |
| `scripts/mqtt/mqtt_stress_async.py` | Async orchestrator, shard manager, and global dashboard bootstrapper. | Start the load test; stop with `Ctrl+C`. |
| `scripts/mqtt/metrics_server.py` | Flask server used internally by the orchestrator (aggregates all shards). | Imported automatically; seldom run standalone. |
| `scripts/mqtt/report_last_run.py` | Prints a friendly summary of the latest run’s JSON report. | Use after each run to capture KPIs. |
| `scripts/mqtt/check_connectivity.py` | Simple REST smoke test (login + one-page device list). | Run before provisioning if unsure about network/credentials. |
| `scripts/mqtt/delete_devices.py` | Deletes devices listed in `data/provisioning/devices.csv`. | Cleanup after lab sessions; also purges the parent server when `TB_PARENT_URL` is set. |
| `scripts/mqtt/delete_by_prefix.py` | Deletes all tenant devices whose name starts with `DEVICE_PREFIX`. | Broad cleanup tool (mirrors deletions to the parent server when configured). |
| `scripts/mqtt/activate_devices.py` | Activa dispositivos y limpia la lista local de deshabilitados. | Antes de las pruebas o para reanudar dispositivos específicos. |
| `scripts/mqtt/deactivate_devices.py` | Desactiva dispositivos y los marca como inactivos en ThingsBoard. | Úsalo al terminar sesiones para evitar envíos residuales. |
| `scripts/mqtt/toggle_devices.py` | Marks devices as manually enabled/disabled and updates the local control file. | Use for maintenance windows or to pause specific simulators. |
| `scripts/mqtt/stop_simulation.py` | Sends a signal to stop the async simulator using the stored PID. | Run when you need to abort an ongoing test remotely. |
| `scripts/mqtt/send_telemetry.py` | Legacy threaded simulator maintained for comparison experiments. | Optional; new tests should use `scripts/mqtt/mqtt_stress_async.py`. |
| `scripts/mqtt/tb.py` | Lightweight ThingsBoard REST client (shared by other scripts). | Internal helper; review if extending functionality. |

VS Code users can leverage `.vscode/tasks.json` and `.vscode/launch.json` for one-click execution.

---

## 10. Customisation Scenarios
| Scenario | Change | Files |
|----------|--------|-------|
| Different fleet size | Update `DEVICE_COUNT` (and optionally `DEVICE_PREFIX`). Re-run `scripts/mqtt/create_devices.py`. | `.env`, `scripts/mqtt/create_devices.py` (auto reads `.env`). |
| Faster/slower publish rate | Modify `PUBLISH_INTERVAL_SEC`. Restart the simulator. | `.env`. |
| TLS-enabled MQTT broker | Set `MQTT_TLS=1` and ensure certificates are trusted (see `paho-mqtt` docs). | `.env`, optionally extend `scripts/mqtt/send_telemetry.py`. |
| Additional telemetry fields | Edit the `payload()` method in `scripts/mqtt/send_telemetry.py`. Maintain JSON-compatible values. | `scripts/mqtt/send_telemetry.py`. |
| Alternate dashboard port | Change `METRICS_PORT` in `.env`. | `.env`. |
| Export metrics to another system | Consume `/api/metrics` or extend `MetricsServer` to push data elsewhere (Prometheus, InfluxDB, etc.). | `scripts/mqtt/metrics_server.py`. |

Always rerun `py -3 -m compileall scripts` (optional) after structural changes to catch syntax errors early.

---

## 11. Troubleshooting & FAQ
- **`tokens.json` missing** - Run `py -3 "scripts/mqtt/create_devices.py"` first.
- **MQTT connection errors** - Verify broker host/port. If using TLS, check certificates and set `MQTT_TLS=1`.
- **Dashboard not reachable** - Ensure the simulator is running, and confirm `METRICS_HOST`/`METRICS_PORT` in `.env`. Run `netstat -ano | findstr :5050` on Windows to check bindings.
- **Pause specific devices** - Use `py -3 scripts/mqtt/toggle_devices.py --devices ...` to disable or re-enable them; the simulator reloads `data/control/disabled_devices.json` automatically.
- **Collapse detected early** - Check `disconnect_causes` on the dashboard/report. Common reasons: broker capacity, network latency, or ThingsBoard rate limits.
- **Need to rerun with clean state** - Use `delete_devices.py` or `delete_by_prefix.py`; both remove devices from the edge and any configured parent server before provisioning again.
- **Scripts require proxy access** - Set `HTTP_PROXY`/`HTTPS_PROXY` environment variables before running any script.

---

## 12. Operational Checklist
1. Populate `.env` with accurate URLs, credentials, and desired fleet parameters.
2. Install dependencies with `py -3 -m pip install -r requirements.txt`.
3. (Optional) Validate REST access using `check_connectivity.py`.
4. Provision devices via `create_devices.py`.
5. Launch the simulator (`scripts/mqtt/mqtt_stress_async.py`) and monitor the global dashboard (default http://localhost:5050).
6. After the test, capture the CLI summary and archive the generated log + JSON report.
7. Clean up devices only if necessary; the cleanup scripts wipe both the edge and any configured parent server.

Following this checklist ensures repeatable, well-documented load tests.

---

## 13. Contributing / Extending
- **New telemetry scenarios** - Fork the payload generator and metrics collector.
- **CI integration** - Wrap the scripts in container jobs or GitHub Actions to run scheduled performance tests.
- **Alerting** - Extend `MetricsServer` to push data to monitoring stacks (Prometheus, Grafana, ELK, etc.).
- **Visualization** - Replace the lightweight Chart.js dashboard with a front-end of choice; the `/api/metrics` endpoint is a stable contract.

For collaboration, keep secrets out of version control, open pull requests with context, and add unit/integration tests if you modify REST/MQTT logic.

---

### Need Help?
If you hit issues not covered here:
- Examine `data/logs/<run-id>.log` for detailed context.
- Compare `data/runs/<run-id>.json` with a known-good run.
- Reach out to the platform team with logs, JSON reports, and `.env` (with sensitive fields redacted).

Happy load testing!
