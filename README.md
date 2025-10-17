# ThingsBoard Telemetry Stress Kit

This toolkit keeps a ThingsBoard tenant populated with up to **500** simulated devices, generates synchronized MQTT bursts to stress the server, and visualises the resulting metrics in an independent dashboard. Each phase—provisioning, telemetry, and dashboard—runs with its own command so you can stop telemetry while the dashboard remains available.

---

## Contents

1. [Requirements](#requirements)  
2. [Environment](#environment)  
3. [Installation](#installation)  
4. [Quick commands](#quick-commands)  
5. [Typical workflow](#typical-workflow)  
6. [Live metrics and dashboard](#live-metrics-and-dashboard)  
7. [Cleaning up devices](#cleaning-up-devices)  
8. [Connectivity check](#connectivity-check)  
9. [Troubleshooting](#troubleshooting)

---

## Requirements

- Python 3.9+ (create a virtual environment if possible).  
- ThingsBoard tenant credentials (`TB_URL`, `TB_USERNAME`, `TB_PASSWORD`).  
- MQTT broker reachable at `MQTT_HOST:MQTT_PORT` (TLS optional).  
- Docker is optional if you prefer to containerise the workflow.

---

## Environment

Configure `.env` with the tenant, MQTT, and simulator details. The system caps the fleet at 500 devices so you can focus on high-load scenarios without overwhelming the tenant.

| Variable | Description |
|----------|-------------|
| `TB_URL` | Base REST URL for ThingsBoard (no trailing slash). |
| `TB_USERNAME`, `TB_PASSWORD` | Tenant credentials. |
| `TB_TIMEOUT` | REST timeout (seconds, default 60). |
| `DEVICE_PREFIX` | Name prefix for simulated devices (default `sim`). |
| `DEVICE_COUNT` | Devices to provision/stream (1 ≤ count ≤ 500). |
| `DEVICE_LABEL`, `DEVICE_TYPE` | Metadata applied to each device. |
| `DEVICE_PROFILE_ID` | Optional profile id; falls back to ThingsBoard default. |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS` | MQTT connection parameters. |
| `PUBLISH_INTERVAL_SEC` | Interval between bursts per device (seconds). |
| `SIM_START_LEAD_TIME` | Lead time before the first synchronized burst (seconds). |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | Bind address for the dashboard (defaults to `0.0.0.0:5000`). |
| `DASHBOARD_REFRESH_MS` | Dashboard polling interval (milliseconds). |
| `PROVISION_RETRIES`, `PROVISION_RETRY_DELAY` | Backoff parameters when creating devices. |

Update `.env` whenever you change credentials, the device cap, or MQTT details, then rerun the relevant command.

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

## Quick commands

| Action | Command (from repo root, after activating the venv) |
|--------|-----------------------------------------------------|
| **Crear/actualizar dispositivos** | `python scripts/provision_devices.py` |
| **Levantar dashboard de métricas** | `python scripts/run_dashboard.py` |
| **Iniciar envío de telemetría** | `python scripts/run_telemetry.py` |
| **Detener envío de telemetría** | `python scripts/stop_telemetry.py` (o `Ctrl+C` en la consola donde corre `run_telemetry.py`) |
| **Borrar dispositivos del ThingsBoard** | `python scripts/delete_by_prefix.py` |
| **Verificar conectividad rápida** | `python scripts/check_connectivity.py` |

All scripts respect the 500-device ceiling automatically.

---

## Typical workflow

1. **Provision devices**  
   ```
   python scripts/provision_devices.py
   ```  
   Stores tokens in `data/tokens.json` and exports metadata to `data/devices.csv`. The metrics file (`data/metrics.json`) is reset to an idle state so the dashboard shows a friendly message until telemetry starts.

2. **Start the dashboard** (optional but recommended before load tests)  
   ```
   python scripts/run_dashboard.py
   ```  
   Visit `http://<DASHBOARD_HOST>:<DASHBOARD_PORT>` to observe live metrics. The dashboard keeps working even if telemetry stops later.

3. **Kick off telemetry**  
   ```
   python scripts/run_telemetry.py
   ```  
   The command launches one MQTT worker per token, synchronises all bursts using a barrier, and writes continuous snapshots to `data/metrics.json`. Console output shows connection issues and aggregate metrics.

4. **Stop telemetry when needed**  
   - Press `Ctrl+C` in the telemetry console, **or**  
   - Run `python scripts/stop_telemetry.py` from a different shell (creates `data/stop.flag`, detected by the simulator).  
   Either option keeps the latest metrics available to the dashboard.

5. **Tear down devices (optional)**  
   ```
   python scripts/delete_by_prefix.py
   ```  
   Deletes every device whose name begins with `DEVICE_PREFIX`, giving you a clean slate for future tests.

---

## Live metrics and dashboard

`run_telemetry.py` feeds the collector that tracks:

- Connected, disconnected, and failed device counts.  
- Total packets sent/failed, average send rate per device, and cumulative data volume (MB).  
- Estimated bandwidth (Mbps), number of active MQTT channels, and collapse time (seconds until the last disconnection).  
- Detailed failure buckets (authentication, network, TLS, payload, memory, etc.) with the stage that triggered them.

`run_dashboard.py` reads `data/metrics.json` on every refresh—there is no in-memory coupling with the telemetry process—so you can keep the dashboard running while telemetry is offline. The UI includes:

- Status banner with the current simulator state (idle, running, stopping, stopped).  
- Timeline chart for connected vs failed/disconnected devices.  
- Metric tiles for throughput, bandwidth, collapse timer, and burst interval.  
- Failure table (sorted by frequency).  
- Device health table (up to 40 recent incidents) highlighting the last telemetry timestamp, failure reason, and stage.

---

## Cleaning up devices

To wipe all simulated devices from the tenant:

```powershell
python scripts/delete_by_prefix.py
```

Every device starting with `DEVICE_PREFIX` is deleted—there is no retention threshold—so you always return to an empty slate. Use this after stress tests or before tweaking prefixes/counts in `.env`.

`data/tokens.json`, `data/devices.csv`, and `data/metrics.json` remain local so you can reprovision on demand.

---

## Connectivity check

For a quick sanity check against the ThingsBoard API:

```powershell
python scripts/check_connectivity.py
```

The script logs in using `.env` credentials and performs a simple device query to confirm REST access before you run heavier operations.

---

## Troubleshooting

- **Errores de conexión MQTT (`rc != 0`)**  
  Revisa credenciales de broker, TLS y reachability. El dashboard clasifica las causas y muestra la etapa (connect/publish/disconnect).

- **Excepciones al provisionar**  
  Ajusta `PROVISION_RETRIES` y `PROVISION_RETRY_DELAY` si ThingsBoard está saturado. El proceso reintenta automáticamente hasta 5 veces por dispositivo.

- **Dashboard sin datos**  
  Abre `http://<host>:<port>/metrics` para ver el JSON bruto. Si `metrics.json` está corrupto, vuelve a ejecutar `run_telemetry.py` o `provision_devices.py` (este último lo reinicia con estado `idle`).

- **Necesitas reiniciar limpio**  
  Ejecuta `stop_telemetry.py`, espera a que el comando de telemetría finalice, lanza `delete_by_prefix.py`, ajusta `.env` si es necesario y repite el flujo desde `provision_devices.py`.

Con estos comandos puedes generar tráfico sincronizado, inspeccionar métricas en tiempo real incluso después de detener la telemetría, y mantener el tenant bajo control durante las pruebas de carga.
