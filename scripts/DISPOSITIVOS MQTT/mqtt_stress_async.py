#!/usr/bin/env python3
"""Async MQTT stress simulator for ThingsBoard."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import io
import json
import math
import os
import random
import signal
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence

import aiofiles
import aiohttp
from asyncio_mqtt import Client, MqttError
import paho.mqtt.client as paho_client
from dotenv import load_dotenv

from metrics_server import MetricsServer, GlobalMetricsCollector


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DEFAULT_TOKENS_FILE = DATA_DIR / "provisioning" / "tokens.json"
LOGS_DIR = DATA_DIR / "logs"
METRICS_DIR = DATA_DIR / "metrics"
DEFAULT_TOPIC = "v1/devices/me/telemetry"

load_dotenv(override=True)


def _split_env_list(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    items: List[str] = []
    for raw in value.replace(",", " ").split():
        cleaned = raw.strip()
        if cleaned:
            items.append(cleaned)
    return items or None


ENV_MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
ENV_MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
ENV_DEVICE_COUNT = int(os.getenv("DEVICE_COUNT", "0"))
ENV_INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SEC", "5"))
ENV_DURATION = float(os.getenv("SIM_DURATION_SEC", os.getenv("SIM_DURATION_SECONDS", "0")))
ENV_RAMP_COUNTS = _split_env_list(os.getenv("RAMP_COUNTS"))
ENV_RAMP_PERCENTAGES = _split_env_list(os.getenv("RAMP_PERCENTAGES"))
ENV_REPORT_INTERVAL = float(os.getenv("REPORT_INTERVAL_SEC", "15"))
ENV_METRICS_HOST = os.getenv("METRICS_HOST", "127.0.0.1")
ENV_METRICS_PORT = int(os.getenv("METRICS_PORT", "5050"))
ENV_METRICS_REFRESH_MS = int(os.getenv("METRICS_REFRESH_MS", "2000"))
ENV_TOPIC = os.getenv("MQTT_TOPIC", DEFAULT_TOPIC)
ENV_QOS = int(os.getenv("MQTT_QOS", "1"))


# Paho MQTT >=2.0 eliminó `message_retry_set`, pero asyncio-mqtt todavía lo invoca.
# Registramos un stub compatible para evitar AttributeError.
if not hasattr(paho_client.Client, "message_retry_set"):
    def _message_retry_set(self: paho_client.Client, _retry: float) -> None:
        self._message_retry = _retry

    paho_client.Client.message_retry_set = _message_retry_set  # type: ignore[attr-defined]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DeviceToken:
    device_id: str
    token: str


class MetricsAggregator:
    def __init__(self, total_devices: int) -> None:
        self.total_devices = total_devices
        self._lock = threading.Lock()
        self.started_at = utcnow()
        self.success_count = 0
        self.failure_count = 0
        self._latencies: List[float] = []
        self._latencies_sorted = True
        self._latencies_cache: List[float] = []
        self._active_devices: set[str] = set()
        self._seen_devices: set[str] = set()
        self._failed_devices: set[str] = set()
        self._messages_per_device: defaultdict[str, int] = defaultdict(int)
        self._failed_messages_per_device: defaultdict[str, int] = defaultdict(int)
        self._bytes_per_device: defaultdict[str, int] = defaultdict(int)
        self.disconnect_causes: Counter[str] = Counter()
        self.bytes_sent = 0
        self.peak_connected = 0
        self.collapsed_at: Optional[datetime] = None
        self.collapse_reason: Optional[str] = None

    def _mark_collapse(self, reason: str) -> None:
        if self.collapsed_at is None:
            self.collapsed_at = utcnow()
            self.collapse_reason = reason

    def record_client_connected(self, device_id: str) -> None:
        with self._lock:
            self._active_devices.add(device_id)
            self._seen_devices.add(device_id)
            self.peak_connected = max(self.peak_connected, len(self._active_devices))

    def record_client_disconnected(self, device_id: str, reason: Optional[str], graceful: bool) -> None:
        with self._lock:
            self._active_devices.discard(device_id)
            if not graceful:
                self._failed_devices.add(device_id)
                if reason:
                    self.disconnect_causes[reason] += 1
                self._mark_collapse(reason or "disconnect")

    def record_publish_success(
        self,
        device_id: str,
        latency_seconds: float,
        payload_bytes: int,
    ) -> None:
        with self._lock:
            self.success_count += 1
            self._latencies.append(latency_seconds)
            self._latencies_sorted = False
            self.bytes_sent += payload_bytes
            self._messages_per_device[device_id] += 1
            self._bytes_per_device[device_id] += payload_bytes

    def record_publish_failure(self, device_id: str, reason: Optional[str]) -> None:
        with self._lock:
            self.failure_count += 1
            self._failed_devices.add(device_id)
            self._failed_messages_per_device[device_id] += 1
            if reason:
                self.disconnect_causes[reason] += 1
            self._mark_collapse(reason or "publish failure")

    def record_connection_failure(self, device_id: str, reason: Optional[str]) -> None:
        with self._lock:
            self.failure_count += 1
            self._failed_devices.add(device_id)
            if reason:
                self.disconnect_causes[reason] += 1
            self._mark_collapse(reason or "connection failure")

    def _ensure_sorted_latencies(self) -> Sequence[float]:
        # Caller must hold the lock.
        if not self._latencies:
            return []
        if not self._latencies_sorted:
            self._latencies_cache = sorted(self._latencies)
            self._latencies_sorted = True
        return self._latencies_cache

    def _percentile(self, percent: float, data: Sequence[float]) -> Optional[float]:
        if not data:
            return None
        if len(data) == 1:
            return data[0]
        rank = (len(data) - 1) * percent / 100.0
        lower_index = math.floor(rank)
        upper_index = math.ceil(rank)
        if lower_index == upper_index:
            return data[lower_index]
        lower = data[lower_index]
        upper = data[upper_index]
        return lower + (upper - lower) * (rank - lower_index)

    def snapshot(self) -> Dict[str, Any]:
        now = utcnow()
        with self._lock:
            elapsed = max((now - self.started_at).total_seconds(), 1e-9)
            latencies_sorted = list(self._ensure_sorted_latencies())
            avg_ms = round(fmean(self._latencies) * 1000, 4) if self._latencies else None
            p50_ms = (
                round(self._percentile(50, latencies_sorted) * 1000, 4)
                if latencies_sorted
                else None
            )
            p95_ms = (
                round(self._percentile(95, latencies_sorted) * 1000, 4)
                if latencies_sorted
                else None
            )
            p99_ms = (
                round(self._percentile(99, latencies_sorted) * 1000, 4)
                if latencies_sorted
                else None
            )
            observed_devices = max(self.total_devices, len(self._seen_devices), 1)
            messages_per_second = self.success_count / elapsed
            bandwidth_mbps = (self.bytes_sent * 8) / elapsed / 1_000_000
            collapse_seconds = (
                (self.collapsed_at - self.started_at).total_seconds()
                if self.collapsed_at
                else None
            )
            return {
                "timestamp": now.isoformat(),
                "uptime_seconds": round(elapsed, 2),
                "total_devices": observed_devices,
                "active_clients": len(self._active_devices),
                "successful_publishes": self.success_count,
                "failed_publishes": self.failure_count,
                "avg_latency_ms": avg_ms,
                "p50_latency_ms": p50_ms,
                "p95_latency_ms": p95_ms,
                "p99_latency_ms": p99_ms,
                "messages_per_second": round(messages_per_second, 4),
                "bandwidth_mbps": round(bandwidth_mbps, 6),
                "elapsed_seconds": elapsed,
                "connected_devices": len(self._active_devices),
                "failed_devices": len(self._failed_devices),
                "messages_sent": self.success_count,
                "messages_failed": self.failure_count,
                "data_volume_mb": self.bytes_sent / (1024 * 1024),
                "avg_send_rate_per_device": self.success_count / elapsed / observed_devices,
                "avg_messages_per_device": self.success_count / observed_devices,
                "channels_in_use": len(self._active_devices),
                "collapse_time_seconds": collapse_seconds,
                "collapse_reason": self.collapse_reason,
                "disconnect_causes": dict(self.disconnect_causes),
            }

    def summary(self) -> Dict[str, Any]:
        snap = self.snapshot()
        with self._lock:
            snap["total_devices"] = max(self.total_devices, len(self._seen_devices))
            snap["peak_connected_devices"] = self.peak_connected
            snap["bytes_sent"] = self.bytes_sent
            snap["disconnect_causes"] = dict(self.disconnect_causes)
        return snap

    def device_breakdown(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._lock:
            devices: List[Dict[str, Any]] = []
            all_devices = set(self._messages_per_device.keys()) | set(
                self._failed_messages_per_device.keys()
            ) | set(
                self._failed_devices
            )
            for device in all_devices:
                devices.append(
                    {
                        "device": device,
                        "messages": self._messages_per_device.get(device, 0),
                        "failed_messages": self._failed_messages_per_device.get(device, 0),
                        "bytes": self._bytes_per_device.get(device, 0),
                    }
                )
            devices.sort(key=lambda item: item["messages"], reverse=True)
            if limit is not None:
                devices = devices[:limit]
            return devices


class AsyncJsonLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._writer())

    async def _writer(self) -> None:
        async with aiofiles.open(self.path, "w", encoding="utf-8") as afp:
            while True:
                record = await self._queue.get()
                if record is None:
                    break
                await afp.write(json.dumps(record, ensure_ascii=False) + "\n")
                await afp.flush()

    async def log(self, record: Dict[str, Any]) -> None:
        await self._queue.put(record)

    async def close(self) -> None:
        await self._queue.put(None)
        if self._task is not None:
            await self._task


class AsyncCsvLogger:
    def __init__(self, path: Path, fieldnames: Sequence[str]) -> None:
        self.path = path
        self.fieldnames = list(fieldnames)
        self._queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._writer())

    async def _writer(self) -> None:
        async with aiofiles.open(self.path, "w", encoding="utf-8", newline="") as afp:
            header_buffer = io.StringIO()
            header_writer = csv.DictWriter(
                header_buffer, fieldnames=self.fieldnames, extrasaction="ignore"
            )
            header_writer.writeheader()
            await afp.write(header_buffer.getvalue())
            await afp.flush()
            while True:
                record = await self._queue.get()
                if record is None:
                    break
                row_buffer = io.StringIO()
                row_writer = csv.DictWriter(
                    row_buffer, fieldnames=self.fieldnames, extrasaction="ignore"
                )
                row_writer.writerow(record)
                await afp.write(row_buffer.getvalue())
                await afp.flush()

    async def log(self, snapshot: Dict[str, Any]) -> None:
        await self._queue.put(snapshot)

    async def close(self) -> None:
        await self._queue.put(None)
        if self._task is not None:
            await self._task


class AggregatorClient:
    def __init__(self, endpoint: str, shard_id: str) -> None:
        self.endpoint = endpoint
        self.shard_id = shard_id
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._session is None:
                self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def send(self, snapshot: Dict[str, Any], devices: List[Dict[str, Any]]) -> None:
        async with self._lock:
            if self._session is None:
                return
            payload = {
                "shard_id": self.shard_id,
                "snapshot": snapshot,
                "devices": devices,
            }
            try:
                async with self._session.post(self.endpoint, json=payload, timeout=5):
                    pass
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] No se pudo reportar métricas al agregador ({exc}).", file=sys.stderr)


class DeviceWorker:
    def __init__(
        self,
        config: DeviceToken,
        host: str,
        port: int,
        topic: str,
        qos: int,
        publish_interval: float,
        metrics: MetricsAggregator,
        event_logger: AsyncJsonLogger,
        stop_event: asyncio.Event,
        backoff_base: float,
        backoff_max: float,
    ) -> None:
        self.config = config
        self.host = host
        self.port = port
        self.topic = topic
        self.qos = qos
        self.interval = publish_interval
        self.metrics = metrics
        self.event_logger = event_logger
        self.stop_event = stop_event
        self.backoff_base = max(backoff_base, 0.1)
        self.backoff_max = max(backoff_max, self.backoff_base)
        self._message_sequence = 0

    def _build_payload(self) -> Dict[str, Any]:
        self._message_sequence += 1
        # Randomized but deterministic-looking telemetry fields.
        return {
            "seq": self._message_sequence,
            "ts": utcnow().isoformat(),
            "temperature": round(random.uniform(18.0, 32.0), 2),
            "humidity": round(random.uniform(30.0, 70.0), 2),
            "voltage": round(random.uniform(210.0, 230.0), 2),
            "status": random.choice(["idle", "active", "maintenance"]),
            "device_id": self.config.device_id,
        }

    async def _publish(self, client: Client) -> bool:
        payload_dict = self._build_payload()
        payload_json = json.dumps(payload_dict, ensure_ascii=False)
        start = perf_counter()
        try:
            await client.publish(self.topic, payload_json, qos=self.qos)
            latency = perf_counter() - start
            payload_size = len(payload_json.encode("utf-8"))
            self.metrics.record_publish_success(self.config.device_id, latency, payload_size)
            await self.event_logger.log(
                {
                    "timestamp": utcnow().isoformat(),
                    "device": self.config.device_id,
                    "event": "publish",
                    "status": "success",
                    "latency_ms": round(latency * 1000, 4),
                    "payload": payload_dict,
                }
            )
            return True
        except MqttError as exc:
            latency = perf_counter() - start
            reason = f"publish:{exc.__class__.__name__}"
            self.metrics.record_publish_failure(self.config.device_id, reason)
            await self.event_logger.log(
                {
                    "timestamp": utcnow().isoformat(),
                    "device": self.config.device_id,
                    "event": "publish",
                    "status": "failure",
                    "error": str(exc),
                    "latency_ms": round(latency * 1000, 4),
                }
            )
            return False

    async def run(self) -> None:
        backoff = self.backoff_base
        while not self.stop_event.is_set():
            connected = False
            disconnect_reason = "unknown"
            try:
                async with Client(
                    self.host,
                    self.port,
                    username=self.config.token,
                    client_id=self.config.device_id,
                ) as client:
                    connected = True
                    disconnect_reason = "graceful"
                    backoff = self.backoff_base
                    self.metrics.record_client_connected(self.config.device_id)
                    await self.event_logger.log(
                        {
                            "timestamp": utcnow().isoformat(),
                            "device": self.config.device_id,
                            "event": "connected",
                            "host": self.host,
                            "port": self.port,
                        }
                    )
                    while not self.stop_event.is_set():
                        published = await self._publish(client)
                        if not published:
                            disconnect_reason = "mqtt_publish_error"
                            break
                        try:
                            await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval)
                        except asyncio.TimeoutError:
                            continue
                        if self.stop_event.is_set():
                            disconnect_reason = "stopped"
                            break
                    if not self.stop_event.is_set() and disconnect_reason == "graceful":
                        disconnect_reason = "loop_exit"
            except asyncio.CancelledError:
                disconnect_reason = "cancelled"
                raise
            except MqttError as exc:
                disconnect_reason = f"mqtt_error:{exc.__class__.__name__}"
                self.metrics.record_connection_failure(self.config.device_id, disconnect_reason)
                await self.event_logger.log(
                    {
                        "timestamp": utcnow().isoformat(),
                        "device": self.config.device_id,
                        "event": "connection_error",
                        "error": str(exc),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                disconnect_reason = f"error:{exc.__class__.__name__}"
                self.metrics.record_connection_failure(self.config.device_id, disconnect_reason)
                await self.event_logger.log(
                    {
                        "timestamp": utcnow().isoformat(),
                        "device": self.config.device_id,
                        "event": "unexpected_error",
                        "error": repr(exc),
                    }
                )
            finally:
                if connected:
                    graceful = disconnect_reason in {"graceful", "loop_exit", "stopped", "cancelled"}
                    self.metrics.record_client_disconnected(
                        self.config.device_id, disconnect_reason, graceful=graceful
                    )
                    await self.event_logger.log(
                        {
                            "timestamp": utcnow().isoformat(),
                            "device": self.config.device_id,
                            "event": "disconnected",
                            "reason": disconnect_reason,
                        }
                    )
            if self.stop_event.is_set():
                break
            if disconnect_reason in {"graceful", "loop_exit", "stopped"}:
                break
            await asyncio.sleep(min(backoff, self.backoff_max))
            backoff = min(backoff * 2, self.backoff_max)


def load_tokens_from_file(path: Path) -> List[DeviceToken]:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de tokens: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    tokens: List[DeviceToken] = []
    if isinstance(data, dict):
        for device_id, token in sorted(data.items()):
            tokens.append(DeviceToken(device_id=str(device_id), token=str(token)))
    elif isinstance(data, list):
        for idx, token in enumerate(data):
            tokens.append(DeviceToken(device_id=f"device_{idx}", token=str(token)))
    else:
        raise ValueError("tokens.json debe contener un objeto (dict) o una lista.")
    return tokens


def generate_tokens(prefix: str, count: int, start_id: int = 0) -> List[DeviceToken]:
    return [
        DeviceToken(device_id=f"{prefix}{idx}", token=f"{prefix}{idx}")
        for idx in range(start_id, start_id + count)
    ]


def select_devices(
    tokens: Sequence[DeviceToken],
    device_count: int,
    start_id: int,
    override_count: Optional[int],
) -> List[DeviceToken]:
    if start_id < 0:
        raise ValueError("--start-id no puede ser negativo.")
    if start_id >= len(tokens):
        raise ValueError("start-id está fuera del rango de tokens disponibles.")
    total_to_take = override_count or device_count or (len(tokens) - start_id)
    if total_to_take <= 0:
        raise ValueError("El número de dispositivos debe ser mayor a cero.")
    end_index = start_id + total_to_take
    if end_index > len(tokens):
        raise ValueError("No hay suficientes tokens para cubrir el rango solicitado.")
    return list(tokens[start_id:end_index])


def parse_ramp(ramp_values: Optional[Sequence[int]], total_devices: int) -> List[int]:
    if not ramp_values:
        return [total_devices]
    ramp_sequence = [int(value) for value in ramp_values]
    if any(value <= 0 for value in ramp_sequence):
        raise ValueError("Todos los valores de la rampa deben ser positivos.")
    if any(later < earlier for earlier, later in zip(ramp_sequence, ramp_sequence[1:])):
        raise ValueError("La rampa debe ser una secuencia no decreciente.")
    if ramp_sequence[-1] > total_devices:
        raise ValueError("El último valor de la rampa no puede exceder el total de dispositivos.")
    if ramp_sequence[-1] < total_devices:
        ramp_sequence.append(total_devices)
    return ramp_sequence


def parse_ramp_percentages(values: Optional[Sequence[str]], total_devices: int) -> List[int]:
    if not values:
        return [total_devices]
    percentages: List[float] = []
    for raw in values:
        text = raw.strip()
        if not text:
            continue
        if text.endswith("%"):
            text = text[:-1]
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"Valor de porcentaje inválido: {raw}") from exc
        if number <= 0:
            raise ValueError("Los porcentajes deben ser mayores que 0.")
        if number <= 1:
            number *= 100
        if number > 100:
            raise ValueError("Los porcentajes no pueden exceder el 100%.")
        percentages.append(number)
    if not percentages:
        return [total_devices]
    if any(later < earlier for earlier, later in zip(percentages, percentages[1:])):
        raise ValueError("Los porcentajes deben ser una secuencia no decreciente.")
    ramp_sequence: List[int] = []
    for pct in percentages:
        count = max(1, math.ceil(total_devices * pct / 100))
        count = min(total_devices, count)
        if ramp_sequence and count < ramp_sequence[-1]:
            count = ramp_sequence[-1]
        ramp_sequence.append(count)
    if ramp_sequence[-1] < total_devices:
        ramp_sequence.append(total_devices)
    return ramp_sequence


def prepare_selected_devices(args: argparse.Namespace) -> List[DeviceToken]:
    if args.tokens_file and Path(args.tokens_file).exists():
        base_tokens = load_tokens_from_file(Path(args.tokens_file))
    elif args.token_prefix:
        synthetic_count = args.count or args.device_count
        if not synthetic_count:
            raise SystemExit("Al usar --token-prefix debes indicar --count o --device-count.")
        base_tokens = generate_tokens(args.token_prefix, synthetic_count, args.start_id)
        args.device_count = synthetic_count
        args.start_id = 0
        args.count = synthetic_count
    else:
        raise SystemExit(
            "Debes proporcionar --tokens-file existente o --token-prefix para generar tokens."
        )

    selected_devices = select_devices(
        base_tokens,
        device_count=args.device_count,
        start_id=args.start_id,
        override_count=args.count,
    )
    if not selected_devices:
        raise SystemExit("No se seleccionaron dispositivos para la simulación.")
    return selected_devices


def configure_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _set_event_from_signal(sig: int) -> None:
        if not stop_event.is_set():
            print(f"Signal {signal.Signals(sig).name} recibido, deteniendo simulación...", file=sys.stderr)
            stop_event.set()

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, f: _set_event_from_signal(s))


async def periodic_reporter(
    metrics: MetricsAggregator,
    csv_logger: AsyncCsvLogger,
    report_interval: float,
    stop_event: asyncio.Event,
    aggregator_client: Optional[AggregatorClient] = None,
) -> None:
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=report_interval)
            break
        except asyncio.TimeoutError:
            snapshot = metrics.snapshot()
            await csv_logger.log(snapshot)
            if aggregator_client is not None:
                devices = metrics.device_breakdown(limit=None)
                await aggregator_client.send(snapshot, devices)
            print(
                (
                    f"[{snapshot['timestamp']}] activos={snapshot['active_clients']}/"
                    f"{snapshot['total_devices']} "
                    f"ok={snapshot['successful_publishes']} "
                    f"fail={snapshot['failed_publishes']} "
                    f"avg={snapshot['avg_latency_ms'] or 'n/a'}ms "
                    f"p95={snapshot['p95_latency_ms'] or 'n/a'}ms "
                    f"p99={snapshot['p99_latency_ms'] or 'n/a'}ms "
                    f"rate={snapshot['messages_per_second']:.4f} msg/s "
                    f"bw={snapshot['bandwidth_mbps']:.4f} Mbps"
                )
            )
    # Final snapshot when stopping
    snapshot = metrics.snapshot()
    await csv_logger.log(snapshot)
    if aggregator_client is not None:
        devices = metrics.device_breakdown(limit=None)
        await aggregator_client.send(snapshot, devices)
    print(
        (
            f"[{snapshot['timestamp']}] resumen final -> ok={snapshot['successful_publishes']}, "
            f"fail={snapshot['failed_publishes']}, avg={snapshot['avg_latency_ms'] or 'n/a'}ms, "
            f"p99={snapshot['p99_latency_ms'] or 'n/a'}ms, "
            f"bw={snapshot['bandwidth_mbps']:.4f} Mbps"
        )
    )


async def run_simulation(args: argparse.Namespace) -> None:
    args.log_dir.mkdir(parents=True, exist_ok=True)
    args.metrics_dir.mkdir(parents=True, exist_ok=True)
    if args.interval <= 0:
        raise SystemExit("--interval debe ser mayor a 0.")
    if args.ramp_wait < 0:
        raise SystemExit("--ramp-wait no puede ser negativo.")
    if args.duration < 0:
        raise SystemExit("--duration no puede ser negativo.")
    if args.device_count < 0:
        raise SystemExit("--device-count no puede ser negativo.")
    if args.count is not None and args.count <= 0:
        raise SystemExit("--count debe ser mayor a 0.")
    if args.ramp and args.ramp_percentages:
        raise SystemExit("Usa --ramp o --ramp-percentages, pero no ambos al mismo tiempo.")
    if not args.disable_dashboard and args.metrics_refresh <= 0:
        raise SystemExit("--metrics-refresh debe ser mayor a 0 cuando el dashboard está habilitado.")

    selected_devices = prepare_selected_devices(args)
    ramp_counts_input = [int(value) for value in args.ramp] if args.ramp else None
    if args.ramp_percentages:
        ramp_sequence = parse_ramp_percentages(args.ramp_percentages, len(selected_devices))
    else:
        ramp_sequence = parse_ramp(ramp_counts_input, total_devices=len(selected_devices))
    metrics = MetricsAggregator(total_devices=len(selected_devices))
    session_id_base = utcnow().strftime("async-run-%Y%m%d-%H%M%S")
    shard_suffix = ""
    if getattr(args, "worker", False):
        shard_suffix = f"-s{args.start_id:05d}-n{len(selected_devices):05d}"
    session_id = f"{session_id_base}{shard_suffix}"
    json_log_path = args.log_dir / f"{session_id}-events.jsonl"
    csv_log_path = args.metrics_dir / f"{session_id}-metrics.csv"
    event_logger = AsyncJsonLogger(json_log_path)
    csv_logger = AsyncCsvLogger(
        csv_log_path,
        [
            "timestamp",
            "uptime_seconds",
            "elapsed_seconds",
            "total_devices",
            "active_clients",
            "connected_devices",
            "successful_publishes",
            "failed_publishes",
            "failed_devices",
            "avg_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "p99_latency_ms",
            "messages_per_second",
            "bandwidth_mbps",
            "avg_send_rate_per_device",
            "avg_messages_per_device",
        ],
    )
    await event_logger.start()
    await csv_logger.start()

    aggregator_client: Optional[AggregatorClient] = None
    if args.aggregator_endpoint:
        shard_identifier = args.shard_id or f"{args.start_id:05d}-{len(selected_devices):05d}"
        aggregator_client = AggregatorClient(args.aggregator_endpoint, shard_identifier)
        await aggregator_client.start()

    metrics_server: Optional[MetricsServer] = None
    if not args.disable_dashboard and args.aggregator_endpoint is None:
        try:
            metrics_server = MetricsServer(
                metrics,
                host=args.metrics_host,
                port=args.metrics_port,
                refresh_interval_ms=args.metrics_refresh,
                profile_id=os.getenv("DEVICE_PROFILE_ID"),
            )
            metrics_server.start()
            host_label = (
                args.metrics_host
                if args.metrics_host not in ("0.0.0.0", "", "127.0.0.1")
                else "localhost"
            )
            print(f"Dashboard disponible en http://{host_label}:{args.metrics_port}")
        except Exception as exc:  # noqa: BLE001
            print(f"No se pudo iniciar el dashboard de métricas: {exc}", file=sys.stderr)
            metrics_server = None

    stop_event = asyncio.Event()
    configure_signal_handlers(stop_event)

    async def _stop_after_duration() -> None:
        if args.duration <= 0:
            return
        await asyncio.sleep(args.duration)
        if not stop_event.is_set():
            print("Tiempo máximo alcanzado, deteniendo simulación...", file=sys.stderr)
            stop_event.set()

    duration_task = asyncio.create_task(_stop_after_duration())
    reporter_task = asyncio.create_task(
        periodic_reporter(
            metrics,
            csv_logger,
            args.report_interval,
            stop_event,
            aggregator_client=aggregator_client,
        )
    )

    device_workers = [
        DeviceWorker(
            config=device,
            host=args.host,
            port=args.port,
            topic=args.topic,
            qos=args.qos,
            publish_interval=args.interval,
            metrics=metrics,
            event_logger=event_logger,
            stop_event=stop_event,
            backoff_base=args.backoff_base,
            backoff_max=args.backoff_max,
        )
        for device in selected_devices
    ]

    tasks: List[asyncio.Task[None]] = []
    launched = 0
    for target in ramp_sequence:
        if stop_event.is_set():
            break
        to_launch = min(target, len(device_workers)) - launched
        for worker in device_workers[launched : launched + to_launch]:
            tasks.append(asyncio.create_task(worker.run()))
        launched += to_launch
        if launched >= len(device_workers):
            break
        if args.ramp_wait > 0 and target != ramp_sequence[-1]:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=args.ramp_wait)
            except asyncio.TimeoutError:
                continue
            break

    if tasks:
        stop_waiter = asyncio.create_task(stop_event.wait())
        _done, pending = await asyncio.wait(
            tasks + [stop_waiter],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not stop_event.is_set():
            stop_event.set()
        for pending_task in pending:
            if pending_task is stop_waiter:
                pending_task.cancel()
        if not stop_waiter.done():
            with contextlib.suppress(asyncio.CancelledError):
                await stop_waiter
        await asyncio.gather(*tasks, return_exceptions=True)
    else:
        await stop_event.wait()
    duration_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await duration_task
    with contextlib.suppress(asyncio.CancelledError):
        await reporter_task
    await event_logger.close()
    await csv_logger.close()
    if aggregator_client is not None:
        await aggregator_client.close()
    if metrics_server is not None:
        metrics_server.stop()
    print(f"Eventos guardados en {json_log_path}")
    print(f"Métricas guardadas en {csv_log_path}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulador de estrés MQTT asincrónico para ThingsBoard."
    )
    parser.add_argument("--host", default=ENV_MQTT_HOST, help="Host del broker MQTT.")
    parser.add_argument("--port", type=int, default=ENV_MQTT_PORT, help="Puerto del broker MQTT.")
    parser.add_argument(
        "--device-count",
        type=int,
        default=ENV_DEVICE_COUNT,
        help="Cantidad total de dispositivos a simular (por defecto usa todos los tokens disponibles).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=ENV_INTERVAL,
        help="Intervalo de publicación de telemetría (segundos).",
    )
    parser.add_argument(
        "--tokens-file",
        type=Path,
        default=DEFAULT_TOKENS_FILE,
        help="Ruta al archivo JSON con tokens.",
    )
    parser.add_argument(
        "--token-prefix",
        type=str,
        default=None,
        help="Prefijo para generar tokens sintéticos cuando no hay archivo JSON.",
    )
    parser.add_argument(
        "--ramp",
        type=int,
        nargs="+",
        default=None,
        help="Secuencia de cantidades de dispositivos para rampas de carga.",
    )
    parser.add_argument(
        "--ramp-percentages",
        nargs="+",
        default=None,
        help="Secuencia de porcentajes (por ejemplo 25 50 100 o 0.25 0.5 1.0) para las rampas.",
    )
    parser.add_argument(
        "--ramp-wait",
        type=float,
        default=0.0,
        help="Segundos de espera entre cada rampa.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=ENV_DURATION,
        help="Tiempo total de prueba en segundos (0 para infinito hasta interrupción).",
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=0,
        help="Offset inicial para seleccionar tokens (para ejecutar múltiples instancias).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Cantidad de dispositivos a tomar desde start-id (distribución de carga).",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=ENV_TOPIC,
        help="Tópico MQTT donde se publica la telemetría.",
    )
    parser.add_argument(
        "--qos",
        type=int,
        choices=[0, 1, 2],
        default=ENV_QOS,
        help="QoS usado para la publicación MQTT.",
    )
    parser.add_argument(
        "--report-interval",
        type=float,
        default=ENV_REPORT_INTERVAL,
        help="Segundos entre reportes periódicos por consola.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=LOGS_DIR,
        help="Directorio donde guardar eventos JSON.",
    )
    parser.add_argument(
        "--metrics-dir",
        type=Path,
        default=METRICS_DIR,
        help="Directorio donde guardar métricas en CSV.",
    )
    parser.add_argument(
        "--backoff-base",
        type=float,
        default=1.0,
        help="Tiempo inicial de backoff para reconexiones (segundos).",
    )
    parser.add_argument(
        "--backoff-max",
        type=float,
        default=30.0,
        help="Tiempo máximo de backoff para reconexiones (segundos).",
    )
    parser.add_argument(
        "--metrics-host",
        type=str,
        default=ENV_METRICS_HOST,
        help="Host para exponer el dashboard Flask de métricas.",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=ENV_METRICS_PORT,
        help="Puerto para el dashboard Flask de métricas.",
    )
    parser.add_argument(
        "--metrics-refresh",
        type=int,
        default=ENV_METRICS_REFRESH_MS,
        help="Intervalo de refresco del dashboard (ms).",
    )
    parser.add_argument(
        "--disable-dashboard",
        action="store_true",
        help="Desactiva el dashboard Flask de métricas.",
    )
    parser.add_argument(
        "--max-clients-per-process",
        type=int,
        default=400,
        help="Máximo de clientes por proceso antes de dividir en múltiples procesos (Windows).",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--aggregator-endpoint",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--shard-id",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.ramp is None and args.ramp_percentages is None:
        if ENV_RAMP_PERCENTAGES:
            args.ramp_percentages = list(ENV_RAMP_PERCENTAGES)
        elif ENV_RAMP_COUNTS:
            args.ramp = [int(value) for value in ENV_RAMP_COUNTS]
    if args.ramp is not None:
        args.ramp = [int(value) for value in args.ramp]
    return args


async def _async_main(args: argparse.Namespace) -> None:
    try:
        await run_simulation(args)
    except KeyboardInterrupt:
        print("Interrupción recibida, terminando simulación...", file=sys.stderr)


def run_worker(args: argparse.Namespace) -> None:
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass
    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("Simulación interrumpida.", file=sys.stderr)


def orchestrate(args: argparse.Namespace) -> None:
    selected_devices = prepare_selected_devices(args)
    total_devices = len(selected_devices)
    del selected_devices
    max_per_process = max(1, args.max_clients_per_process)

    split_required = sys.platform.startswith("win") and total_devices > max_per_process
    if not split_required:
        args.worker = True
        run_worker(args)
        return

    script_path = Path(__file__).resolve()
    base_start = args.start_id
    processes: List[tuple[int, subprocess.Popen]] = []
    aggregator_server: Optional[MetricsServer] = None
    aggregator_endpoint: Optional[str] = None
    collector: Optional[GlobalMetricsCollector] = None
    global_summary: Optional[Dict[str, Any]] = None

    print(
        f"Dividiendo la simulación en procesos de hasta {max_per_process} clientes "
        f"(total={total_devices})."
    )

    try:
        aggregator_host = "127.0.0.1"
        aggregator_port = args.metrics_port
        collector = GlobalMetricsCollector()
        aggregator_server = MetricsServer(
            collector,
            host=args.metrics_host,
            port=aggregator_port,
            refresh_interval_ms=args.metrics_refresh,
            profile_id=os.getenv("DEVICE_PROFILE_ID"),
        )
        aggregator_server.start()
        host_label = (
            args.metrics_host
            if args.metrics_host not in ("0.0.0.0", "", "127.0.0.1")
            else "localhost"
        )
        print(f"Dashboard global disponible en http://{host_label}:{aggregator_port}")
        aggregator_endpoint = f"http://{aggregator_host}:{aggregator_port}/api/shard"

        for shard_index, offset in enumerate(range(0, total_devices, max_per_process)):
            shard_count = min(max_per_process, total_devices - offset)
            shard_start = base_start + offset
            cmd: List[str] = [sys.executable, str(script_path), "--worker"]

            cmd.extend(["--start-id", str(shard_start)])
            cmd.extend(["--count", str(shard_count)])
            cmd.extend(["--device-count", str(shard_count)])
            cmd.extend(["--host", args.host])
            cmd.extend(["--port", str(args.port)])
            cmd.extend(["--interval", str(args.interval)])
            cmd.extend(["--duration", str(args.duration)])
            cmd.extend(["--report-interval", str(args.report_interval)])
            cmd.extend(["--topic", args.topic])
            cmd.extend(["--qos", str(args.qos)])
            cmd.extend(["--log-dir", str(args.log_dir)])
            cmd.extend(["--metrics-dir", str(args.metrics_dir)])
            cmd.extend(["--backoff-base", str(args.backoff_base)])
            cmd.extend(["--backoff-max", str(args.backoff_max)])
            cmd.extend(["--ramp-wait", str(args.ramp_wait)])

            if args.tokens_file:
                cmd.extend(["--tokens-file", str(args.tokens_file)])
            if args.token_prefix:
                cmd.extend(["--token-prefix", args.token_prefix])

            if args.ramp:
                cmd.append("--ramp")
                cmd.extend(str(value) for value in args.ramp)
            if args.ramp_percentages:
                cmd.append("--ramp-percentages")
                cmd.extend(args.ramp_percentages)

            if args.metrics_host:
                cmd.extend(["--metrics-host", args.metrics_host])

            cmd.append("--disable-dashboard")

            cmd.extend(["--metrics-refresh", str(args.metrics_refresh)])
            cmd.extend(["--max-clients-per-process", str(args.max_clients_per_process)])
            if aggregator_endpoint:
                cmd.extend(["--aggregator-endpoint", aggregator_endpoint])
                shard_identifier = f"{shard_start:05d}-{shard_count:05d}"
                cmd.extend(["--shard-id", shard_identifier])

            print(
                f"Iniciando shard {shard_index + 1}: start={shard_start}, count={shard_count}"
            )
            proc = subprocess.Popen(cmd)
            processes.append((shard_index, proc))

        exit_codes: List[int] = []
        for shard_index, proc in processes:
            code = proc.wait()
            exit_codes.append(code)
            if code != 0:
                print(
                    f"[WARN] Shard {shard_index + 1} finalizó con código {code}.",
                    file=sys.stderr,
                )

        if collector is not None:
            global_summary = collector.summary()
            print(
                (
                    "Resumen global | dispositivos={total} conectados={connected} "
                    "activos={active} ok={ok} fail={fail} avg={avg}ms "
                    "p99={p99}ms rate={rate:.4f} msg/s"
                ).format(
                    total=global_summary["total_devices"],
                    connected=global_summary["connected_devices"],
                    active=global_summary["active_clients"],
                    ok=global_summary["successful_publishes"],
                    fail=global_summary["failed_publishes"],
                    avg=global_summary["avg_latency_ms"] if global_summary["avg_latency_ms"] is not None else "n/a",
                    p99=global_summary["p99_latency_ms"] if global_summary["p99_latency_ms"] is not None else "n/a",
                    rate=global_summary["messages_per_second"],
                )
            )

        if any(code != 0 for code in exit_codes):
            raise SystemExit("Al menos un shard terminó con errores. Revisa los registros.")
    except KeyboardInterrupt:
        print("Interrupción recibida, deteniendo shards...", file=sys.stderr)
        for _, proc in processes:
            proc.terminate()
        raise
    finally:
        if aggregator_server is not None:
            aggregator_server.stop()


def main() -> None:
    args = parse_args()
    if args.worker:
        run_worker(args)
    else:
        orchestrate(args)


if __name__ == "__main__":
    main()


# Instrucciones de uso:
# - Ajusta en .env: DEVICE_COUNT, SIM_DURATION_SEC y RAMP_PERCENTAGES (por ejemplo 25 50 100).
# 1. python -m venv .venv
# 2. .venv\Scripts\activate (Windows) o source .venv/bin/activate (Linux/Mac)
# 3. pip install -r requirements.txt
# 4. python scripts/mqtt_stress_async.py --host 127.0.0.1 --port 1883 --tokens-file data/provisioning/tokens.json \
#    --device-count 1000 --ramp-percentages 25 50 100 --ramp-wait 180 --interval 5 --duration 1200
