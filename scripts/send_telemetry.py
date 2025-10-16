#!/usr/bin/env python3
"""Spawn MQTT simulators for every stored ThingsBoard token."""
from __future__ import annotations

import json
import os
import random
import signal
import threading
from threading import Barrier, BrokenBarrierError
import time
from pathlib import Path
from typing import Dict

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[1]
TOKENS_FILE = ROOT / "data" / "tokens.json"

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TLS = os.getenv("MQTT_TLS", "0") == "1"
INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SEC", "3"))

RUNNING = True


def stop(_sig, _frame) -> None:
    global RUNNING
    RUNNING = False
    print("\n[INFO] Cerrando simulación...")


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


class SimLoop(threading.Thread):
    def __init__(self, name: str, token: str, barrier: Barrier):
        super().__init__(daemon=True)
        self.name = name
        self.token = token
        self.barrier = barrier
        client_id = f"sim-{name}-{random.randint(1, 1_000_000)}"
        self.client = mqtt.Client(client_id=client_id, clean_session=True)
        self.client.username_pw_set(self.token)
        if MQTT_TLS:
            self.client.tls_set()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, _client, _userdata, _flags, rc) -> None:
        if rc == 0:
            print(f"[MQTT] {self.name} conectado")
        else:
            print(f"[MQTT] {self.name} error rc={rc}")

    def on_disconnect(self, _client, _userdata, rc) -> None:
        print(f"[MQTT] {self.name} desconectado rc={rc}")

    def run(self) -> None:
        try:
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            self.client.loop_start()
            try:
                self.barrier.wait()
            except BrokenBarrierError:
                return
            while RUNNING:
                payload = self.payload()
                self.client.publish("v1/devices/me/telemetry", json.dumps(payload), qos=1)
                time.sleep(INTERVAL)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {self.name}: {exc}")
        finally:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def payload(self) -> Dict:
        return {
            "temperature": round(random.uniform(20.0, 30.0), 2),
            "humidity": random.randint(35, 65),
            "battery": round(random.uniform(3.6, 4.2), 2),
            "status": random.choice(["ok", "ok", "ok", "warn"]),
            "device": self.name,
        }


def main() -> None:
    if not TOKENS_FILE.exists():
        raise SystemExit(f"No existe {TOKENS_FILE}. Ejecuta primero create_devices.py")

    tokens = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    total = len(tokens)
    if total == 0:
        raise SystemExit("tokens.json no contiene dispositivos")
    barrier = Barrier(total + 1)
    loops = [SimLoop(name, token, barrier) for name, token in tokens.items()]

    print(f"[INFO] Lanzando {total} clientes MQTT hacia {MQTT_HOST}:{MQTT_PORT} (TLS={MQTT_TLS})...")

    for loop in loops:
        loop.start()

    try:
        barrier.wait()
        print("[INFO] Primera ráfaga de telemetría disparada.")
    except BrokenBarrierError:
        print("[WARN] No todos los clientes se sincronizaron; revisa los logs de MQTT.")

    while RUNNING:
        time.sleep(0.5)

    for loop in loops:
        loop.join(timeout=5)

    print("[OK] Simulación detenida.")


if __name__ == "__main__":
    main()
