#!/usr/bin/env python3
"""Create ThingsBoard devices and stream telemetry right away."""
from __future__ import annotations

import json
import os
import random
import signal
import string
import threading
from threading import Barrier, BrokenBarrierError
import time
from pathlib import Path
from typing import Dict

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TOKENS_FILE = DATA_DIR / "tokens.json"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")
DEVICE_PREFIX = os.getenv("DEVICE_PREFIX", "sim")
DEVICE_COUNT = int(os.getenv("DEVICE_COUNT", "50"))
DEVICE_LABEL = os.getenv("DEVICE_LABEL", "sim-lab")
DEVICE_TYPE = os.getenv("DEVICE_TYPE", "sensor")
PROFILE_ID = os.getenv("DEVICE_PROFILE_ID")
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TLS = os.getenv("MQTT_TLS", "0") == "1"
INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SEC", "3"))

RUNNING = True


def stop(_sig, _frame) -> None:
    global RUNNING
    RUNNING = False
    print("\n[INFO] Señal recibida: cerrando...")


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


def fail(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}")
    raise SystemExit(code)


class SimLoop(threading.Thread):
    def __init__(self, name: str, token: str, barrier: Barrier):
        super().__init__(daemon=True)
        self.name = name
        self.token = token
        self.barrier = barrier
        suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
        self.client = mqtt.Client(client_id=f"sim-{name}-{suffix}")
        self.client.username_pw_set(self.token)
        if MQTT_TLS:
            try:
                self.client.tls_set()
            except Exception:  # noqa: BLE001
                pass
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, _client, _userdata, _flags, rc) -> None:
        if rc == 0:
            print(f"[MQTT] {self.name} conectado")
        else:
            print(f"[MQTT] {self.name} error de conexión rc={rc}")

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


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("Faltan TB_URL/TB_USERNAME/TB_PASSWORD en .env")

    ensure_dir(DATA_DIR)
    barrier = Barrier(DEVICE_COUNT + 1)
    workers: list[SimLoop] = []
    tokens_map: dict[str, str] = {}

    try:
        with TB(TB_URL, TB_USERNAME, TB_PASSWORD) as api:
            api.login()
            profile_id = PROFILE_ID or api.default_profile()
            if profile_id:
                msg = "fijado" if PROFILE_ID else "por defecto"
                print(f"[INFO] Device Profile {msg}: {profile_id}")
            else:
                print("[INFO] No se encontró Device Profile por defecto")

            for idx in range(1, DEVICE_COUNT + 1):
                name = f"{DEVICE_PREFIX}-{idx:03d}"
                print(f"[INFO] Creando/recuperando '{name}'...")
                device = api.save_device(
                    name,
                    label=DEVICE_LABEL,
                    dev_type=DEVICE_TYPE,
                    profile_id=profile_id,
                )
                dev_id = device["id"]["id"]
                token = api.token(dev_id)
                tokens_map[name] = token
                worker = SimLoop(name, token, barrier)
                worker.start()
                workers.append(worker)
                api.set_attrs(
                    dev_id,
                    {
                        "batch": "sim-" + time.strftime("%Y%m%d"),
                        "group": DEVICE_PREFIX,
                        "index": idx,
                    },
                )
                time.sleep(0.05)

    except (TBError, requests.RequestException) as exc:
        barrier.abort()
        fail(str(exc))

    TOKENS_FILE.write_text(json.dumps(tokens_map, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[INFO] Lanzados {len(workers)} workers. Enviando telemetría a {MQTT_HOST}:{MQTT_PORT} (TLS={MQTT_TLS})"
    )

    try:
        barrier.wait()
        print("[INFO] Primera ráfaga de telemetría disparada.")
    except BrokenBarrierError:
        print("[WARN] No todos los workers se sincronizaron; revisa los logs de MQTT.")

    try:
        while RUNNING:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("[INFO] Deteniendo workers...")
        for worker in workers:
            worker.join(timeout=2)
        print("[OK] Simulación finalizada.")


if __name__ == "__main__":
    main()
