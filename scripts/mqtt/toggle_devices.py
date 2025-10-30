#!/usr/bin/env python3
"""Activates or deactivates ThingsBoard devices and syncs local disabled list."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from dotenv import load_dotenv

from tb import TB, TBError


load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PROVISION_DIR = DATA_DIR / "provisioning"
CONTROL_DIR = DATA_DIR / "control"
CSV_FILE = PROVISION_DIR / "devices.csv"
DISABLED_FILE = CONTROL_DIR / "disabled_devices.json"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_devices(csv_path: Path) -> Dict[str, Dict[str, str]]:
    if not csv_path.exists():
        raise SystemExit(f"No se encontró {csv_path}. Ejecuta primero create_devices.py.")
    devices: Dict[str, Dict[str, str]] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = row.get("name") or row.get("device_name")
            dev_id = row.get("device_id") or row.get("id")
            if not name or not dev_id:
                continue
            devices[name] = {"id": dev_id, "label": row.get("label", ""), "token": row.get("access_token", "")}
    if not devices:
        raise SystemExit(f"{csv_path} no contiene dispositivos válidos.")
    return devices


def target_devices(
    devices_map: Dict[str, Dict[str, str]],
    explicit: Iterable[str] | None,
    prefix: str | None,
    include_all: bool,
) -> List[str]:
    if explicit:
        targets = []
        for name in explicit:
            if name not in devices_map:
                print(f"[WARN] {name} no está en el CSV; se intentará buscarlo vía API.", file=sys.stderr)
            targets.append(name)
        return targets
    if prefix:
        return [name for name in devices_map if name.startswith(prefix)]
    if include_all:
        return sorted(devices_map.keys())
    raise SystemExit("Debes indicar --devices, --prefix o --all.")


def load_disabled(path: Path) -> Set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Archivo inválido {path}: {exc}") from exc
    if isinstance(data, dict):
        items = data.get("disabled", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return {str(item) for item in items}


def save_disabled(path: Path, disabled: Set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "disabled": sorted(disabled),
        "updated_at": utcnow(),
        "note": "Archivo generado por toggle_devices.py. Editar con cuidado.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_device(api: TB, name: str) -> Tuple[str, str]:
    device = api.device(name)
    if not device:
        raise TBError(f"No se encontró el dispositivo {name} en ThingsBoard.")
    dev_id = device["id"]["id"]
    label = device.get("label", "")
    return dev_id, label


def toggle_device(api: TB, dev_id: str, enable: bool) -> bool:
    attrs = {
        "manual_enabled": enable,
        "manual_state": "enabled" if enable else "disabled",
        "manual_updated_at": utcnow(),
    }
    return api.set_attrs(dev_id, attrs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Activa o desactiva dispositivos ThingsBoard.")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--devices",
        nargs="+",
        help="Nombres exactos de los dispositivos a modificar.",
    )
    target_group.add_argument(
        "--prefix",
        help="Aplica el cambio a todos los dispositivos cuyo nombre comience con el prefijo indicado.",
    )
    target_group.add_argument(
        "--all",
        action="store_true",
        help="Aplica el cambio a todos los dispositivos del CSV.",
    )
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--activate", action="store_true", help="Marca los dispositivos como activos.")
    action_group.add_argument("--deactivate", action="store_true", help="Marca los dispositivos como inactivos.")
    parser.add_argument(
        "--csv",
        type=Path,
        default=CSV_FILE,
        help=f"Ruta al CSV de provisión (default {CSV_FILE}).",
    )
    parser.add_argument(
        "--disabled-file",
        type=Path,
        default=DISABLED_FILE,
        help="Archivo JSON usado por el simulador para omitir dispositivos desactivados.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué sucedería sin aplicar cambios en ThingsBoard ni en el archivo local.",
    )
    return parser.parse_args()


def execute_toggle(
    *,
    enable: bool,
    devices: Iterable[str] | None,
    prefix: str | None,
    include_all: bool,
    csv_path: Path,
    disabled_file: Path,
    dry_run: bool,
) -> None:
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        raise SystemExit("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")

    devices_map = load_devices(csv_path)
    targets = target_devices(devices_map, devices, prefix, include_all)
    if not targets:
        print("[INFO] No se encontraron dispositivos que coincidan con los criterios proporcionados.")
        return

    action_label = "Activando" if enable else "Desactivando"
    print(f"[INFO] {action_label} {len(targets)} dispositivo(s)...")

    disabled_set = load_disabled(disabled_file)

    if dry_run:
        for name in targets:
            state = "ON" if enable else "OFF"
            print(f"[DRY] {name} -> {state}")
        future_disabled = set(disabled_set)
        if enable:
            future_disabled.difference_update(targets)
        else:
            future_disabled.update(targets)
        print(f"[DRY] El archivo local quedaría con {len(future_disabled)} desactivados.")
        return

    updated = 0
    missing = 0
    with TB(TB_URL, TB_USERNAME, TB_PASSWORD) as api:
        api.login()
        for name in targets:
            try:
                info = devices_map.get(name)
                if info is None:
                    dev_id, label = fetch_device(api, name)
                else:
                    dev_id = info["id"]
                    label = info.get("label", "")
                toggle_device(api, dev_id, enable)
                if enable:
                    disabled_set.discard(name)
                else:
                    disabled_set.add(name)
                print(f"[OK] {name} ({label}) -> {'activo' if enable else 'inactivo'}")
                updated += 1
            except TBError as exc:
                print(f"[ERR] {name}: {exc}", file=sys.stderr)
                missing += 1

    save_disabled(disabled_file, disabled_set)
    print(f"[INFO] Archivo actualizado: {disabled_file}")
    if missing:
        print(f"[WARN] {missing} dispositivo(s) no pudieron actualizarse; revisa los mensajes anteriores.")
    print(f"[DONE] {updated} dispositivo(s) procesados correctamente.")


def main() -> None:
    parsed = parse_args()
    execute_toggle(
        enable=parsed.activate,
        devices=parsed.devices,
        prefix=parsed.prefix,
        include_all=parsed.all,
        csv_path=parsed.csv,
        disabled_file=parsed.disabled_file,
        dry_run=parsed.dry_run,
    )


if __name__ == "__main__":
    main()
