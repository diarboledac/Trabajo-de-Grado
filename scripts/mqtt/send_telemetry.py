#!/usr/bin/env python3
"""MQTT simulator with real-time metrics collection and dashboard."""
from __future__ import annotations

import json
import logging
import os
import random
import signal
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from metrics_server import MetricsServer

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PROVISION_DIR = DATA_DIR / "provisioning"
RUNS_DIR = DATA_DIR / "runs"
LOGS_DIR = DATA_DIR / "logs"
TOKENS_FILE = PROVISION_DIR / "tokens.json"

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TLS = os.getenv("MQTT_TLS", "0") == "1"
INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SEC", "3"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def classify_disconnect(rc: int) -> str:
    if rc == mqtt.MQTT_ERR_SUCCESS:
        return "graceful shutdown"
    mapping = {
        mqtt.MQTT_ERR_CONN_LOST: "connection lost",
        mqtt.MQTT_ERR_NO_CONN: "no connection to broker",
        mqtt.MQTT_ERR_PROTOCOL: "protocol violation",
        mqtt.MQTT_ERR_QUEUE_SIZE: "internal queue full",
        mqtt.MQTT_ERR_TLS: "tls handshake failure",
    }
    return mapping.get(rc, mqtt.error_string(rc))


def classify_exception(exc: BaseException) -> str:
    import socket

    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return "network"
    if isinstance(exc, MemoryError):
        return "memory"
    return exc.__class__.__name__


@dataclass
class TelemetryMetrics:
    messages_sent: int = 0
    first_publish_at: Optional[datetime] = None
    last_publish_at: Optional[datetime] = None
    last_payload: Optional[Dict[str, object]] = None
    disconnects: List[Dict[str, object]] = field(default_factory=list)
    errors: List[Dict[str, object]] = field(default_factory=list)
    connected_at: Optional[datetime] = None
    last_issue: Optional[str] = None


class MetricsCollector:
    def __init__(self, total_devices: int):
        self.total_devices = total_devices
        self._lock = threading.Lock()
        self.started_at = utcnow()
        self.connected_now: Set[str] = set()
        self.seen_devices: Set[str] = set()
        self.failed_devices: Set[str] = set()
        self.messages_sent = 0
        self.messages_failed = 0
        self.bytes_sent = 0
        self.peak_connected = 0
        self.collapsed_at: Optional[datetime] = None
        self.collapse_reason: Optional[str] = None
        self.messages_per_device: Dict[str, int] = defaultdict(int)
        self.bytes_per_device: Dict[str, int] = defaultdict(int)
        self.failed_messages_per_device: Dict[str, int] = defaultdict(int)
        self.disconnect_causes: Counter[str] = Counter()

    def _mark_collapse(self, reason: str) -> None:
        if self.collapsed_at is None:
            self.collapsed_at = utcnow()
            self.collapse_reason = reason

    def record_connect(self, device: str) -> None:
        with self._lock:
            self.connected_now.add(device)
            self.seen_devices.add(device)
            self.peak_connected = max(self.peak_connected, len(self.connected_now))

    def record_disconnect(self, device: str, graceful: bool, reason: Optional[str]) -> None:
        with self._lock:
            self.connected_now.discard(device)
            if not graceful:
                self.failed_devices.add(device)
                if reason:
                    self.disconnect_causes[reason] += 1
                self._mark_collapse(reason or "disconnect")

    def record_message_sent(self, device: str, payload_bytes: int) -> None:
        with self._lock:
            self.messages_sent += 1
            self.bytes_sent += payload_bytes
            self.messages_per_device[device] += 1
            self.bytes_per_device[device] += payload_bytes

    def record_message_failed(self, device: str, reason: Optional[str]) -> None:
        with self._lock:
            self.messages_failed += 1
            self.failed_devices.add(device)
            self.failed_messages_per_device[device] += 1
            if reason:
                self.disconnect_causes[reason] += 1
            self._mark_collapse(reason or "publish failure")

    def record_runtime_error(self, device: str, reason: str) -> None:
        with self._lock:
            self.failed_devices.add(device)
            self.disconnect_causes[reason] += 1
            self._mark_collapse(reason)

    def snapshot(self) -> Dict[str, object]:
        now = utcnow()
        with self._lock:
            elapsed = max((now - self.started_at).total_seconds(), 1e-9)
            observed_devices = max(len(self.seen_devices), self.total_devices, 1)
            avg_rate = self.messages_sent / elapsed / observed_devices
            avg_messages = self.messages_sent / observed_devices
            messages_per_second = self.messages_sent / elapsed
            bandwidth_mbps = (self.bytes_sent * 8) / elapsed / 1_000_000
            collapse_seconds = (
                (self.collapsed_at - self.started_at).total_seconds()
                if self.collapsed_at
                else None
            )
            top_senders = sorted(
                self.messages_per_device.items(), key=lambda item: item[1], reverse=True
            )[:10]
            top_failures = sorted(
                self.failed_messages_per_device.items(), key=lambda item: item[1], reverse=True
            )[:10]
            return {
                "elapsed_seconds": elapsed,
                "connected_devices": len(self.connected_now),
                "failed_devices": len(self.failed_devices),
                "messages_sent": self.messages_sent,
                "messages_failed": self.messages_failed,
                "data_volume_mb": self.bytes_sent / (1024 * 1024),
                "avg_send_rate_per_device": avg_rate,
                "avg_messages_per_device": avg_messages,
                "messages_per_second": messages_per_second,
                "bandwidth_mbps": bandwidth_mbps,
                "channels_in_use": len(self.connected_now),
                "collapse_time_seconds": collapse_seconds,
                "collapse_reason": self.collapse_reason,
                "top_senders": top_senders,
                "top_failures": top_failures,
                "disconnect_causes": dict(self.disconnect_causes.most_common(10)),
            }

    def summary(self) -> Dict[str, object]:
        snap = self.snapshot()
        with self._lock:
            snap["total_devices"] = self.total_devices
            snap["peak_connected_devices"] = self.peak_connected
            snap["bytes_sent"] = self.bytes_sent
            snap["disconnect_causes"] = dict(self.disconnect_causes)
        return snap

    def device_breakdown(self, limit: Optional[int] = None) -> List[Dict[str, object]]:
        with self._lock:
            devices: List[Dict[str, object]] = []
            for device, count in self.messages_per_device.items():
                devices.append(
                    {
                        "device": device,
                        "messages": count,
                        "failed_messages": self.failed_messages_per_device.get(device, 0),
                        "bytes": self.bytes_per_device.get(device, 0),
                    }
                )
            devices.sort(key=lambda item: item["messages"], reverse=True)
            if limit is not None:
                devices = devices[:limit]
            return devices


class MetricsReporter(threading.Thread):
    def __init__(self, collector: MetricsCollector, interval: float = 10.0):
        super().__init__(daemon=True, name="metrics-reporter")
        self.collector = collector
        self.interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            snapshot = self.collector.snapshot()
            LOGGER.info(
                (
                    "Metrics | connected=%d failed=%d msgs=%d failed_msgs=%d "
                    "bandwidth=%.3fMbps avg_rate=%.3f msg/s"
                ),
                snapshot["connected_devices"],
                snapshot["failed_devices"],
                snapshot["messages_sent"],
                snapshot["messages_failed"],
                snapshot["bandwidth_mbps"],
                snapshot["avg_send_rate_per_device"],
            )


RUNNING = threading.Event()
RUNNING.set()
LOGGER = logging.getLogger("tb_simulator")
thread_barrier: Optional[threading.Barrier] = None


def setup_logging(session_id: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{session_id}.log"
    LOGGER.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    LOGGER.addHandler(console_handler)

    return log_path


class SimLoop(threading.Thread):
    def __init__(self, name: str, token: str, session_id: str, collector: MetricsCollector):
        super().__init__(daemon=True, name=f"sim-{name}")
        self.name = name
        self.token = token
        self.session_id = session_id
        self.collector = collector
        suffix = random.randint(1, 1_000_000)
        self.client = mqtt.Client(client_id=f"{session_id}-{name}-{suffix}", clean_session=True)
        self.client.username_pw_set(self.token)
        if MQTT_TLS:
            self.client.tls_set()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.metrics = TelemetryMetrics()
        self._sequence = 0
        self._lock = threading.Lock()

    def on_connect(self, _client, _userdata, _flags, rc) -> None:
        reason = mqtt.connack_string(rc)
        if rc == 0:
            self.metrics.connected_at = utcnow()
            LOGGER.info("MQTT connected: %s (%s)", self.name, reason)
            self.collector.record_connect(self.name)
        else:
            self.record_error("connect", reason, rc)

    def on_disconnect(self, _client, _userdata, rc) -> None:
        reason = classify_disconnect(rc)
        event = {"timestamp": iso(utcnow()), "code": rc, "reason": reason}
        with self._lock:
            self.metrics.disconnects.append(event)
            self.metrics.last_issue = reason
        graceful = rc == mqtt.MQTT_ERR_SUCCESS
        self.collector.record_disconnect(self.name, graceful, reason)
        level = logging.INFO if graceful else logging.WARNING
        LOGGER.log(level, "MQTT disconnected: %s (%s)", self.name, reason)

    def record_error(self, stage: str, message: str, code: Optional[int] = None) -> None:
        event = {
            "timestamp": iso(utcnow()),
            "stage": stage,
            "message": message,
            "code": code,
        }
        with self._lock:
            self.metrics.errors.append(event)
            self.metrics.last_issue = message
        if stage != "publish":
            self.collector.record_runtime_error(self.name, f"{stage}: {message}")
        LOGGER.warning("%s error: %s | %s", self.name, stage, message)

    def payload(self) -> Dict[str, object]:
        self._sequence += 1
        cpu_usage = round(random.uniform(18.0, 75.0), 2)
        memory_usage = round(random.uniform(120.0, 350.0), 1)
        latency_ms = round(random.uniform(15.0, 250.0), 1)
        issue = None
        status = "ok"
        if random.random() < 0.05:
            issue = random.choice(
                ["network-latency", "sensor-drift", "low-battery", "high-cpu", "memory-pressure"]
            )
            status = "warn" if issue != "low-battery" else "critical"
        return {
            "timestamp": iso(utcnow()),
            "device": self.name,
            "sequence": self._sequence,
            "temperature": round(random.uniform(20.0, 30.0), 2),
            "humidity": random.randint(35, 65),
            "battery": round(random.uniform(3.4, 4.2), 2),
            "cpu_usage_percent": cpu_usage,
            "memory_usage_mb": memory_usage,
            "network_latency_ms": latency_ms,
            "status": status,
            "issue": issue,
        }

    def run(self) -> None:
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
            if thread_barrier is None:
                raise RuntimeError("barrier not initialized")
            thread_barrier.wait()
            while RUNNING.is_set():
                payload = self.payload()
                payload_json = json.dumps(payload)
                payload_bytes = len(payload_json.encode("utf-8"))
                info = self.client.publish("v1/devices/me/telemetry", payload_json, qos=1)
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    now = utcnow()
                    with self._lock:
                        self.metrics.messages_sent += 1
                        self.metrics.last_payload = payload
                        if not self.metrics.first_publish_at:
                            self.metrics.first_publish_at = now
                        self.metrics.last_publish_at = now
                        self.metrics.last_issue = None
                    self.collector.record_message_sent(self.name, payload_bytes)
                else:
                    reason = mqtt.error_string(info.rc)
                    self.record_error("publish", reason, info.rc)
                    self.collector.record_message_failed(self.name, reason)
                time.sleep(INTERVAL)
        except Exception as exc:  # noqa: BLE001
            reason = classify_exception(exc)
            self.record_error("runtime", f"{reason}: {exc.__class__.__name__}")
        finally:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass


def stop(_sig, _frame) -> None:
    if RUNNING.is_set():
        LOGGER.info("Stop signal received, finishing simulation...")
        RUNNING.clear()


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


def load_tokens() -> Dict[str, str]:
    if not TOKENS_FILE.exists():
        raise SystemExit(f"No existe {TOKENS_FILE}. Ejecuta scripts/create_devices.py primero.")
    return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))


def ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PROVISION_DIR.mkdir(parents=True, exist_ok=True)


def summarize(
    session_id: str,
    started_at: datetime,
    loops: List[SimLoop],
    log_path: Path,
    collector: MetricsCollector,
) -> Path:
    ensure_dirs()
    ended_at = utcnow()
    summary = {
        "session_id": session_id,
        "started_at": iso(started_at),
        "ended_at": iso(ended_at),
        "duration_seconds": round((ended_at - started_at).total_seconds(), 2),
        "mqtt": {
            "host": MQTT_HOST,
            "port": MQTT_PORT,
            "tls": MQTT_TLS,
        },
        "interval_seconds": INTERVAL,
        "device_count": len(loops),
        "messages_sent": 0,
        "log_file": str(log_path.relative_to(ROOT)),
        "devices": {},
    }
    for loop in loops:
        metrics = loop.metrics
        summary["messages_sent"] += metrics.messages_sent
        summary["devices"][loop.name] = {
            "messages_sent": metrics.messages_sent,
            "connected_at": iso(metrics.connected_at),
            "first_publish_at": iso(metrics.first_publish_at),
            "last_publish_at": iso(metrics.last_publish_at),
            "last_payload": metrics.last_payload,
            "disconnects": metrics.disconnects,
            "errors": metrics.errors,
            "last_issue": metrics.last_issue,
        }

    summary["metrics"] = collector.summary()

    report_path = RUNS_DIR / f"{session_id}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = RUNS_DIR / "latest.json"
    latest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("Resumen escrito en %s", report_path.relative_to(ROOT))
    return report_path


def main() -> None:
    tokens = load_tokens()
    if not tokens:
        raise SystemExit("tokens.json no contiene dispositivos.")

    session_id = utcnow().strftime("run-%Y%m%d-%H%M%S")
    log_path = setup_logging(session_id)

    total = len(tokens)
    LOGGER.info(
        "Lanzando %d clientes MQTT hacia %s:%s (TLS=%s, intervalo=%.2fs)",
        total,
        MQTT_HOST,
        MQTT_PORT,
        MQTT_TLS,
        INTERVAL,
    )

    global thread_barrier
    thread_barrier = threading.Barrier(total + 1)
    collector = MetricsCollector(total)
    metrics_host = os.getenv("METRICS_HOST", "0.0.0.0")
    metrics_port = int(os.getenv("METRICS_PORT", "5050"))
    refresh_ms = int(os.getenv("METRICS_REFRESH_MS", "2000"))
    profile_target = os.getenv("DEVICE_PROFILE_ID") or "3a022cf0-aae1-11f0-bea7-7bc7d3c79da2"

    loops = [
        SimLoop(name, token, session_id, collector) for name, token in sorted(tokens.items())
    ]
    reporter = MetricsReporter(collector, interval=10.0)

    metrics_server: Optional[MetricsServer] = None
    try:
        metrics_server = MetricsServer(
            collector,
            host=metrics_host,
            port=metrics_port,
            refresh_interval_ms=refresh_ms,
            profile_id=profile_target,
        )
        metrics_server.start()
        host_label = metrics_host if metrics_host not in ("0.0.0.0", "", "127.0.0.1") else "localhost"
        LOGGER.info("Servidor de metricas en http://%s:%d", host_label, metrics_port)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("No se pudo iniciar el servidor de metricas: %s", exc)
        metrics_server = None

    started_at = utcnow()
    for loop in loops:
        loop.start()
    reporter.start()

    try:
        thread_barrier.wait(timeout=30)
        LOGGER.info("Primera rafaga de telemetria disparada.")
    except Exception:  # noqa: BLE001
        LOGGER.warning("No todos los clientes sincronizaron la primera publicacion.")

    try:
        while RUNNING.is_set():
            time.sleep(0.5)
    finally:
        for loop in loops:
            loop.join(timeout=5)
        reporter.stop()
        reporter.join(timeout=5)
        if metrics_server is not None:
            metrics_server.stop()

    report_path = summarize(session_id, started_at, loops, log_path, collector)
    metrics_summary = collector.summary()
    LOGGER.info(
        (
            "Simulacion finalizada | connected_current=%d connected_peak=%d failed_devices=%d "
            "packets_sent=%d packets_failed=%d volume=%.2fMB bandwidth=%.3fMbps "
            "msgs_per_sec=%.3f avg_msgs_per_device=%.2f avg_rate_per_device=%.3f "
            "collapse_seconds=%s collapse_reason=%s"
        ),
        metrics_summary["connected_devices"],
        metrics_summary["peak_connected_devices"],
        metrics_summary["failed_devices"],
        metrics_summary["messages_sent"],
        metrics_summary["messages_failed"],
        metrics_summary["data_volume_mb"],
        metrics_summary.get("bandwidth_mbps", 0.0),
        metrics_summary.get("messages_per_second", 0.0),
        metrics_summary.get("avg_messages_per_device", 0.0),
        metrics_summary["avg_send_rate_per_device"],
        metrics_summary["collapse_time_seconds"],
        metrics_summary["collapse_reason"],
    )
    LOGGER.info("Reporte: %s", report_path.relative_to(ROOT))


if __name__ == "__main__":
    main()
