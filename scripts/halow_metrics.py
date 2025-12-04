#!/usr/bin/env python3
"""Recolección de métricas HaLow (Tube-AHM) vía SSH.

Nota: el acceso es PC -> Edge -> Tube por LAN; no impacta el enlace HaLow Tube<->Router.
Variables de entorno requeridas:
  HALOW_HOST, HALOW_USER, HALOW_PASSWORD
Dependencia: paramiko (pip install paramiko)
"""
from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import paramiko

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "metrics"

# Estos valores pueden cargarse más tarde (load_dotenv en run_stress_suite), así que siempre leemos del entorno.
HALOW_HOST = os.getenv("HALOW_HOST")
HALOW_USER = os.getenv("HALOW_USER")
HALOW_PASSWORD = os.getenv("HALOW_PASSWORD")
HALOW_IFACE = os.getenv("HALOW_INTERFACE", "halow0")


def _connect_ssh() -> paramiko.SSHClient:
    host = os.getenv("HALOW_HOST", HALOW_HOST)
    user = os.getenv("HALOW_USER", HALOW_USER)
    password = os.getenv("HALOW_PASSWORD", HALOW_PASSWORD)
    if not host or not user or not password:
        raise RuntimeError("HALOW_HOST, HALOW_USER y HALOW_PASSWORD deben estar definidos.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=5)
    return client


def _run(cmd: str, ssh_client: paramiko.SSHClient) -> str:
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    return stdout.read().decode(errors="ignore")


def _parse_iw_info(output: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Extrae RSSI/noise/bitrate de `iwinfo <iface> info`."""
    rssi = noise = bitrate = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Signal:"):
            # Signal: -72 dBm  Noise: -95 dBm
            parts = line.replace("Signal:", "").replace("Noise:", "").replace("dBm", "").split()
            if parts:
                try:
                    rssi = float(parts[0])
                    if len(parts) >= 2:
                        noise = float(parts[1])
                except ValueError:
                    pass
        if "Bit Rate" in line or "Bitrate" in line:
            for token in line.split():
                if token.replace(".", "", 1).isdigit():
                    try:
                        bitrate = float(token)
                        break
                    except ValueError:
                        continue
    return rssi, noise, bitrate


def _parse_iw_link(output: str) -> Tuple[Optional[float], Optional[float]]:
    """Extrae RX/TX bitrate de `iw dev <iface> link`."""
    tx = rx = None
    for line in output.splitlines():
        line = line.strip().lower()
        if line.startswith("tx bitrate"):
            for token in line.split():
                if token.replace(".", "", 1).isdigit():
                    tx = float(token)
                    break
        if line.startswith("rx bitrate"):
            for token in line.split():
                if token.replace(".", "", 1).isdigit():
                    rx = float(token)
                    break
    return tx, rx


def _parse_dev_stats(output: str, iface: str) -> Tuple[Optional[int], Optional[int]]:
    """Lee /proc/net/dev para bytes tx/rx."""
    for line in output.splitlines():
        if line.strip().startswith(f"{iface}:"):
            _, rest = line.split(":", 1)
            fields = rest.split()
            try:
                rx_bytes = int(fields[0])
                tx_bytes = int(fields[8])
                return rx_bytes, tx_bytes
            except (ValueError, IndexError):
                return None, None
    return None, None


def _resolve_iface(ssh_client: paramiko.SSHClient) -> str:
    """Elige interfaz: prioridad env, luego halow*, luego primera interfaz encontrada."""
    env_iface = os.getenv("HALOW_INTERFACE", HALOW_IFACE)
    try:
        iw_out = _run("iw dev", ssh_client)
        interfaces = []
        for line in iw_out.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                iface = line.split()[1]
                interfaces.append(iface)
        if env_iface in interfaces:
            return env_iface
        for iface in interfaces:
            if iface.startswith("halow"):
                return iface
        if interfaces:
            return interfaces[0]
    except Exception:
        pass
    return env_iface


def get_halow_metrics(ssh_client: Optional[paramiko.SSHClient] = None) -> Dict[str, Any]:
    """Obtiene métricas del Tube-AHM vía SSH. No lanza excepción si falla; devuelve parciales."""
    close_client = False
    metrics: Dict[str, Any] = {}
    try:
        if ssh_client is None:
            ssh_client = _connect_ssh()
            close_client = True
        iface = _resolve_iface(ssh_client)
        try:
            iwinfo_out = _run(f"iwinfo {iface} info", ssh_client)
            rssi, noise, bitrate = _parse_iw_info(iwinfo_out)
            metrics["halow_rssi_dbm"] = rssi
            metrics["halow_noise_dbm"] = noise
            metrics["halow_tx_rate_mbps"] = bitrate
        except Exception as exc:  # noqa: BLE001
            logging.warning("No se pudo obtener iwinfo: %s", exc)

        try:
            iwlink_out = _run(f"iw dev {iface} link", ssh_client)
            tx_b, rx_b = _parse_iw_link(iwlink_out)
            if tx_b is not None:
                metrics["halow_tx_rate_mbps"] = tx_b
            if rx_b is not None:
                metrics["halow_rx_rate_mbps"] = rx_b
        except Exception as exc:  # noqa: BLE001
            logging.warning("No se pudo obtener iw link: %s", exc)

        try:
            dev_out = _run("cat /proc/net/dev", ssh_client)
            rx_bytes, tx_bytes = _parse_dev_stats(dev_out, iface)
            metrics["halow_rx_bytes"] = rx_bytes
            metrics["halow_tx_bytes"] = tx_bytes
        except Exception as exc:  # noqa: BLE001
            logging.warning("No se pudo obtener /proc/net/dev: %s", exc)

        # Opcional: station dump para retries/drops
        try:
            station_out = _run(f"iw dev {iface} station dump", ssh_client)
            retries = drops = None
            for line in station_out.splitlines():
                if "retry" in line.lower():
                    tokens = line.split()
                    for token in tokens:
                        if token.isdigit():
                            retries = int(token)
                            break
                if "drop" in line.lower():
                    tokens = line.split()
                    for token in tokens:
                        if token.isdigit():
                            drops = int(token)
                            break
            metrics["halow_retries"] = retries
            metrics["halow_drops"] = drops
        except Exception as exc:  # noqa: BLE001
            logging.warning("No se pudo obtener station dump: %s", exc)
    finally:
        if close_client and ssh_client is not None:
            try:
                ssh_client.close()
            except Exception:
                pass
    return metrics


def collect_halow_metrics_loop(session_id: str, interval_sec: float = 2.0, stop_event: Optional[threading.Event] = None) -> None:
    """Recolector en loop; escribe CSV en data/metrics/halow_<session_id>.csv."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"halow_{session_id}.csv"
    header = [
        "timestamp",
        "halow_rssi_dbm",
        "halow_noise_dbm",
        "halow_tx_rate_mbps",
        "halow_rx_rate_mbps",
        "halow_tx_bytes",
        "halow_rx_bytes",
        "halow_retries",
        "halow_drops",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            ts = datetime.now().isoformat()
            try:
                data = get_halow_metrics()
            except Exception as exc:  # noqa: BLE001
                logging.warning("Colector HaLow: no se pudieron obtener métricas: %s", exc)
                data = {}
            row = {"timestamp": ts}
            row.update({k: data.get(k) for k in header if k != "timestamp"})
            writer.writerow(row)
            handle.flush()
            time.sleep(max(interval_sec, 0.5))
