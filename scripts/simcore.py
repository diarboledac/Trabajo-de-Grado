#!/usr/bin/env python3
"""Shared simulator utilities for the ThingsBoard telemetry stress kit."""
from __future__ import annotations

import json
import os
import random
import string
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TOKENS_FILE = DATA_DIR / "tokens.json"
METRICS_FILE = DATA_DIR / "metrics.json"
STOP_FILE = DATA_DIR / "stop.flag"

MAX_DEVICES = 500


@dataclass(frozen=True)
class SimulatorConfig:
    """Configuration resolved from environment variables."""

    tb_url: str
    tb_username: str
    tb_password: str
    device_prefix: str
    device_count: int
    device_label: str
    device_type: str
    device_profile_id: Optional[str]
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool
    publish_interval: float
    dashboard_host: str
    dashboard_port: int
    dashboard_refresh_ms: int
    provision_retries: int
    provision_retry_delay: float
    start_lead_time: float

    @staticmethod
    def load() -> "SimulatorConfig":
        requested = int(os.getenv("DEVICE_COUNT", str(MAX_DEVICES)))
        device_count = max(1, min(requested, MAX_DEVICES))
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


@dataclass
class DeviceStats:
    """Mutable state captured for each simulated device."""

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

    def to_dict(self, *, status: str, extra: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        payload = {
            "status": status,
            "timestamp": self.timestamp,
            "device_count": self.device_count,
            "interval_sec": self.interval_sec,
            "connected_devices": self.connected_devices,
            "disconnected_devices": self.disconnected_devices,
            "failed_devices": self.failed_devices,
            "failed_or_disconnected": self.failed_or_disconnected,
            "active_channels": self.active_channels,
            "total_packets_sent": self.total_packets_sent,
            "total_packets_failed": self.total_packets_failed,
            "total_volume_mb": self.total_volume_mb,
            "avg_send_rate_per_device": self.avg_send_rate_per_device,
            "bandwidth_mbps": self.bandwidth_mbps,
            "collapse_time": self.collapse_time,
            "failure_breakdown": self.failure_breakdown,
            "devices": [asdict(device) for device in self.devices],
        }
        if extra:
            payload.update(extra)
        return payload


def classify_failure(stage: str, rc: int | mqtt.MQTTErrorCode | None = None, exc: Exception | None = None) -> tuple[str, str]:
    """Map low-level MQTT results into human-readable buckets."""

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
    """Thread-safe aggregator for telemetry statistics."""

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
            if not self._connected and self._collapse_time is None:
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
                stats.total_failures += 1
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
        if not self._connected and self._collapse_time is None:
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


class MetricsReporter(threading.Thread):
    """Periodic logger that prints aggregate metrics during the simulation."""

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
                for reason, count in sorted(
                    snap.failure_breakdown.items(), key=lambda item: item[1], reverse=True
                )[:4]
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


class MetricsWriter(threading.Thread):
    """Persist metrics snapshots to disk so the dashboard can read them independently."""

    def __init__(
        self,
        collector: MetricsCollector,
        status_getter: Callable[[], str],
        *,
        interval: float = 2.0,
        extra_provider: Optional[Callable[[], Dict[str, object]]] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._collector = collector
        self._status_getter = status_getter
        self._interval = interval
        self._extra_provider = extra_provider
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self.write_snapshot()

    def write_snapshot(self) -> None:
        snapshot = self._collector.snapshot()
        status = self._status_getter()
        extra = self._extra_provider() if self._extra_provider else None
        write_metrics_file(snapshot, status=status, extra=extra)

    def stop(self, *, final_status: Optional[str] = None, extra: Optional[Dict[str, object]] = None) -> None:
        self._stop_event.set()
        snapshot = self._collector.snapshot()
        status = final_status if final_status is not None else self._status_getter()
        write_metrics_file(snapshot, status=status, extra=extra)


class StopSignal:
    """Coordinate graceful shutdown between threads and external stop requests."""

    def __init__(self, flag_path: Path) -> None:
        self._flag_path = flag_path
        self._event = threading.Event()

    def _check_flag(self) -> None:
        if self._flag_path.exists():
            self._event.set()

    def is_set(self) -> bool:
        self._check_flag()
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        self._check_flag()
        if self._event.is_set():
            return True
        return self._event.wait(timeout)

    def sleep(self, timeout: float) -> bool:
        """Sleep for up to timeout seconds. Returns True if stop was triggered."""
        if timeout <= 0:
            return self.is_set()
        return self.wait(timeout)

    def trip(self) -> None:
        self._event.set()

    def clear_flag(self) -> None:
        try:
            if self._flag_path.exists():
                self._flag_path.unlink()
        except OSError:
            pass


class StartCoordinator:
    """Share a start timestamp so each device fires simultaneously."""

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
    """Telemetry worker that publishes MQTT messages for a single device."""

    def __init__(
        self,
        name: str,
        token: str,
        config: SimulatorConfig,
        barrier: threading.Barrier,
        start_coordinator: StartCoordinator,
        metrics: MetricsCollector,
        stop_signal: StopSignal,
    ) -> None:
        super().__init__(daemon=True, name=f"SimLoop-{name}")
        self.name = name
        self.token = token
        self.config = config
        self.barrier = barrier
        self.start_coordinator = start_coordinator
        self.metrics = metrics
        self.stop_signal = stop_signal
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
            rc_val = int(rc)
            if rc_val != int(mqtt.MQTT_ERR_SUCCESS):
                reason, detail = classify_failure("connect", rc=rc_val)
                print(f"[ERR] {self.name}: fallo en connect ({detail})")
                self.metrics.record_connect_failure(self.name, rc_val)
                return
            self.client.loop_start()
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                return
            start_time = self.start_coordinator.wait()
            next_tick = start_time
            tick_index = 0
            while not self.stop_signal.is_set():
                wait_time = next_tick - time.perf_counter()
                if self.stop_signal.sleep(wait_time):
                    break
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

    def payload(self) -> Dict[str, object]:
        return {
            "temperature": round(random.uniform(20.0, 30.0), 2),
            "humidity": random.randint(35, 65),
            "battery": round(random.uniform(3.6, 4.2), 2),
            "status": random.choice(["ok", "ok", "ok", "warn"]),
            "device": self.name,
        }


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, data: Dict[str, object]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def write_metrics_file(snapshot: MetricsSnapshot, *, status: str, extra: Optional[Dict[str, object]] = None) -> None:
    ensure_data_dir()
    data = snapshot.to_dict(status=status, extra=extra)
    write_json_atomic(METRICS_FILE, data)


def write_idle_metrics(message: str) -> None:
    ensure_data_dir()
    payload = {
        "status": "idle",
        "message": message,
        "timestamp": time.time(),
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
    write_json_atomic(METRICS_FILE, payload)


def load_tokens(limit: int | None = None) -> Dict[str, str]:
    ensure_data_dir()
    if not TOKENS_FILE.exists():
        return {}
    try:
        tokens = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
        if not isinstance(tokens, dict):
            return {}
    except json.JSONDecodeError:
        return {}
    if limit is not None:
        items = list(tokens.items())[:limit]
        return dict(items)
    return tokens


__all__ = [
    "SimulatorConfig",
    "MAX_DEVICES",
    "DATA_DIR",
    "TOKENS_FILE",
    "METRICS_FILE",
    "STOP_FILE",
    "DeviceStats",
    "DeviceStatusSnapshot",
    "MetricsSnapshot",
    "MetricsCollector",
    "MetricsReporter",
    "MetricsWriter",
    "StopSignal",
    "StartCoordinator",
    "SimLoop",
    "classify_failure",
    "ensure_data_dir",
    "write_metrics_file",
    "write_idle_metrics",
    "load_tokens",
]
