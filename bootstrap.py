#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootstrap.py
Crea un proyecto de simulación ThingsBoard en VS Code y lo ejecuta.
- Genera estructura, archivos y .env apuntando a 192.168.1.175
- Crea entorno virtual, instala dependencias
- Crea 50 dispositivos y lanza la simulación MQTT

Uso:
  python bootstrap.py            # equivale a --all
  python bootstrap.py --setup    # solo prepara entorno/archivos
  python bootstrap.py --create   # crea dispositivos (requiere setup previo)
  python bootstrap.py --telemetry# lanza simulación MQTT (requiere tokens)
  python bootstrap.py --all      # setup + create + telemetry
"""
from __future__ import annotations
import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from getpass import getpass

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SCRIPTS_DIR = ROOT / "scripts"
VSCODE_DIR = ROOT / ".vscode"
VENVDIR = ROOT / ".venv"

def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENVDIR / "Scripts" / "python.exe"
    else:
        return VENVDIR / "bin" / "python"

def write_file(path: Path, content: str, *, overwrite: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        return
    path.write_text(content, encoding="utf-8")
    print(f"[WRITE] {path.relative_to(ROOT)}")

def parse_env_file(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def ensure_env(existing: dict | None = None) -> dict:
    env_path = ROOT / ".env"
    defaults = {
        "TB_URL": "http://192.168.1.145:8080",
        "TB_USERNAME": "",
        "TB_PASSWORD": "",
        "DEVICE_PREFIX": "sim",
        "DEVICE_COUNT": "20",
        "DEVICE_LABEL": "sim-lab",
        "DEVICE_TYPE": "sensor",
        "MQTT_HOST": "192.168.1.145",
        "MQTT_PORT": "1883",
        "MQTT_TLS": "0",
        "PUBLISH_INTERVAL_SEC": "3",
    }
    if existing:
        defaults.update(existing)

    if env_path.exists():
        env_now = parse_env_file(env_path)
        # Si faltan credenciales, pídelas y reescribe .env conservando el resto
        if not env_now.get("TB_USERNAME") or not env_now.get("TB_PASSWORD"):
            print("Completemos las credenciales de ThingsBoard (se guardan en .env local).")
            tb_user = input("TB_USERNAME (email de Tenant): ").strip()
            tb_pass = getpass("TB_PASSWORD: ").strip()
            env_now["TB_USERNAME"] = tb_user
            env_now["TB_PASSWORD"] = tb_pass
            # fusiona con defaults para cualquier clave faltante
            for k, v in defaults.items():
                env_now.setdefault(k, v)
            lines = [f"{k}={v}" for k, v in env_now.items()]
            write_file(env_path, "\n".join(lines))
            return env_now
        print(f"[SKIP] .env ya existe y tiene credenciales.")
        return env_now

    # .env no existe: crearlo con credenciales
    print("Configuremos acceso a ThingsBoard (se guarda en .env local).")
    tb_user = input("TB_USERNAME (email de Tenant): ").strip()
    tb_pass = getpass("TB_PASSWORD: ").strip()
    defaults["TB_USERNAME"] = tb_user
    defaults["TB_PASSWORD"] = tb_pass
    lines = [f"{k}={v}" for k, v in defaults.items()]
    write_file(env_path, "\n".join(lines), overwrite=True)
    return defaults

def create_requirements():
    content = dedent("""\
    requests>=2.31
    paho-mqtt>=1.6
    python-dotenv>=1.0
    """)
    write_file(ROOT / "requirements.txt", content)

def create_vscode_files():
    launch = dedent("""\
    {
      "version": "0.2.0",
      "configurations": [
        {
          "name": "Create devices",
          "type": "python",
          "request": "launch",
          "program": "${workspaceFolder}/scripts/create_devices.py",
          "envFile": "${workspaceFolder}/.env"
        },
        {
          "name": "Send telemetry",
          "type": "python",
          "request": "launch",
          "program": "${workspaceFolder}/scripts/send_telemetry.py",
          "envFile": "${workspaceFolder}/.env"
        }
      ]
    }
    """)
    tasks = dedent("""\
    {
      "version": "2.0.0",
      "tasks": [
        {
          "label": "Run All (bootstrap)",
          "type": "shell",
          "command": "${command:python.interpreterPath}",
          "args": ["bootstrap.py", "--all"],
          "problemMatcher": []
        }
      ]
    }
    """)
    write_file(VSCODE_DIR / "launch.json", launch)
    write_file(VSCODE_DIR / "tasks.json", tasks)

def create_readme():
    content = dedent("""\
    # ThingsBoard Lab (VS Code)
    
    Proyecto listo para:
    - Crear 50 dispositivos en ThingsBoard (Raspberry en 192.168.1.175)
    - Enviar telemetría MQTT desde tu PC
    
    ## Requisitos
    - Python 3.9+
    - Acceso a TB en http://192.168.1.175:8080 (usuario Tenant)
    - Puertos accesibles desde tu PC: 8080 (REST/UI), 1883 (MQTT)
    
    ## Pasos rápidos (VS Code)
    1. Abre esta carpeta en VS Code.
    2. Ejecuta **Run and Debug → Create devices** para crear dispositivos y tokens.
    3. Ejecuta **Run and Debug → Send telemetry** para simular MQTT.
    
    ### Alternativa con tareas
    - Abre *Terminal → Run Task...* y ejecuta **Run All (bootstrap)** para hacer todo de una.
    
    ## Variables (.env)
    - TB_URL, TB_USERNAME, TB_PASSWORD
    - DEVICE_PREFIX, DEVICE_COUNT, DEVICE_LABEL, DEVICE_TYPE, DEVICE_PROFILE_ID
    - MQTT_HOST, MQTT_PORT, MQTT_TLS, PUBLISH_INTERVAL_SEC
    
    ## Seguridad
    - Cambia a TLS (MQTT_TLS=1) y usa 8883 cuando tengas certificados.
    - No subas .env a repositorios públicos.
    """)
    write_file(ROOT / "README.md", content)

TB_HELPERS_PY = r'''#!/usr/bin/env python3
"""ThingsBoard helper client with short, shared calls."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

TIMEOUT = 15


class TBError(RuntimeError):
    """Raised when the remote ThingsBoard API returns an error."""


@dataclass
class TB:
    base: str
    user: str
    password: str
    timeout: int = TIMEOUT

    def __post_init__(self) -> None:
        base = self.base.rstrip("/")
        if not base or not self.user or not self.password:
            raise TBError("Se requieren TB_URL, TB_USERNAME y TB_PASSWORD")
        self.base = base
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "TB":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.close()

    # API helpers ---------------------------------------------------------
    def login(self) -> str:
        resp = self.session.post(
            f"{self.base}/api/auth/login",
            json={"username": self.user, "password": self.password},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise TBError(f"Login fallido: {resp.status_code} {resp.text}")
        token = resp.json().get("token")
        if not token:
            raise TBError("No se obtuvo token JWT")
        self.session.headers.update({"X-Authorization": f"Bearer {token}"})
        return token

    def default_profile(self) -> Optional[str]:
        for endpoint in ("deviceProfileInfos", "deviceProfiles"):
            resp = self.session.get(
                f"{self.base}/api/{endpoint}?pageSize=100&page=0",
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    if item.get("default"):
                        return item["id"]["id"]
        return None

    def device(self, name: str) -> Optional[Dict[str, Any]]:
        resp = self.session.get(
            f"{self.base}/api/tenant/devices?deviceName={name}",
            timeout=self.timeout,
        )
        if resp.status_code == 200 and resp.text and resp.text != "null":
            return resp.json()
        resp = self.session.get(
            f"{self.base}/api/tenant/devices?limit=100&page=0&textSearch={name}",
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                if item.get("name") == name:
                    return item
        return None

    def save_device(
        self,
        name: str,
        *,
        label: str,
        dev_type: str,
        profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": name, "label": label, "type": dev_type}
        if profile_id:
            payload["deviceProfileId"] = {"id": profile_id, "entityType": "DEVICE_PROFILE"}
        resp = self.session.post(
            f"{self.base}/api/device",
            json=payload,
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 400 and "already" in resp.text.lower():
            existing = self.device(name)
            if existing:
                return existing
        raise TBError(f"No se pudo crear o recuperar '{name}': {resp.status_code} {resp.text}")

    def token(self, device_id: str) -> str:
        resp = self.session.get(
            f"{self.base}/api/device/{device_id}/credentials",
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise TBError(f"Error credenciales: {resp.status_code} {resp.text}")
        data = resp.json()
        if data.get("credentialsType") != "ACCESS_TOKEN":
            raise TBError("Credencial no es ACCESS_TOKEN")
        token = data.get("credentialsId")
        if not token:
            raise TBError("credentialsId vacío")
        return token

    def set_attrs(self, device_id: str, attrs: Dict[str, Any]) -> bool:
        resp = self.session.post(
            f"{self.base}/api/plugins/telemetry/DEVICE/{device_id}/SERVER_SCOPE",
            json=attrs,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            print(f"[WARN] No se guardaron attrs para {device_id}: {resp.status_code} {resp.text}")
            return False
        return True


__all__ = ["TB", "TBError"]
'''

CREATE_DEVICES_PY = r'''#!/usr/bin/env python3
"""Create (or reuse) ThingsBoard devices and dump their tokens."""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TOKENS_FILE = DATA_DIR / "tokens.json"
CSV_FILE = DATA_DIR / "devices.csv"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")
DEVICE_PREFIX = os.getenv("DEVICE_PREFIX", "sim")
DEVICE_COUNT = int(os.getenv("DEVICE_COUNT", "50"))
DEVICE_LABEL = os.getenv("DEVICE_LABEL", "sim-lab")
DEVICE_TYPE = os.getenv("DEVICE_TYPE", "sensor")
PROFILE_ID = os.getenv("DEVICE_PROFILE_ID")


def fail(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")

    ensure_dir(DATA_DIR)

    tokens_map: dict[str, str] = {}
    rows: list[list[str]] = []
    started = time.time()

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
                rows.append([dev_id, name, device.get("label", ""), token])
                api.set_attrs(
                    dev_id,
                    {
                        "batch": "sim-" + time.strftime("%Y%m%d"),
                        "group": DEVICE_PREFIX,
                        "index": idx,
                    },
                )

    except TBError as exc:
        fail(str(exc))

    TOKENS_FILE.write_text(json.dumps(tokens_map, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["device_id", "name", "label", "access_token"])
        writer.writerows(rows)

    elapsed = time.time() - started
    print(
        f"[OK] {len(rows)} dispositivos. Tokens en: {TOKENS_FILE}. CSV en: {CSV_FILE}. Tiempo: {elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
'''

SEND_TELEMETRY_PY = r'''#!/usr/bin/env python3
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

load_dotenv()

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
    print("
[INFO] Cerrando simulación...")


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
'''

DELETE_DEVICES_PY = r'''#!/usr/bin/env python3
"""Delete ThingsBoard devices listed in data/devices.csv."""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
CSV_FILE = ROOT / "data" / "devices.csv"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")


def fail(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def delete_device(api: TB, dev_id: str) -> None:
    resp = api.session.delete(f"{api.base}/api/device/{dev_id}", timeout=api.timeout)
    if resp.status_code != 200:
        print(f"[WARN] No se pudo borrar {dev_id}: {resp.status_code} {resp.text}")
    else:
        print(f"[OK] Borrado {dev_id}")


def main() -> None:
    if not CSV_FILE.exists():
        fail(f"No existe {CSV_FILE}")
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")

    try:
        with TB(TB_URL, TB_USERNAME, TB_PASSWORD) as api:
            api.login()
            with CSV_FILE.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    delete_device(api, row["device_id"])
    except TBError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
'''

def create_scripts():
    write_file(SCRIPTS_DIR / "tb.py", TB_HELPERS_PY)
    write_file(SCRIPTS_DIR / "create_devices.py", CREATE_DEVICES_PY)
    write_file(SCRIPTS_DIR / "send_telemetry.py", SEND_TELEMETRY_PY)
    write_file(SCRIPTS_DIR / "delete_devices.py", DELETE_DEVICES_PY)

def create_env_and_files():
    ensure_env()
    create_requirements()
    create_readme()
    create_vscode_files()
    create_scripts()

def make_venv_and_install():
    if VENVDIR.exists():
        print("[INFO] .venv ya existe")
    else:
        print("[INFO] Creando .venv...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENVDIR)])
    py = str(venv_python())
    print("[INFO] Instalando requirements...")
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])

def run_create_devices():
    py = str(venv_python())
    subprocess.check_call([py, str(SCRIPTS_DIR / "create_devices.py")])

def run_send_telemetry():
    py = str(venv_python())
    subprocess.check_call([py, str(SCRIPTS_DIR / "send_telemetry.py")])

def main():
    parser = argparse.ArgumentParser(description="Bootstrap ThingsBoard Lab (VS Code)")
    parser.add_argument("--setup", action="store_true", help="Solo prepara entorno/archivos")
    parser.add_argument("--create", action="store_true", help="Crea dispositivos")
    parser.add_argument("--telemetry", action="store_true", help="Lanza simulación MQTT")
    parser.add_argument("--all", action="store_true", help="Setup + create + telemetry (por defecto)")
    args = parser.parse_args()
    if not any([args.setup, args.create, args.telemetry, args.all]):
        args.all = True

    # Generar archivos + .env
    create_env_and_files()

    # Preparar entorno
    if args.setup or args.all:
        make_venv_and_install()

    # Crear dispositivos
    if args.create or args.all:
        run_create_devices()

    # Simulación MQTT (bloqueante hasta Ctrl+C)
    if args.telemetry or args.all:
        print("[INFO] Lanzando simulación. Ctrl+C para detener.")
        run_send_telemetry()

if __name__ == "__main__":
    main()
