#!/usr/bin/env python3
"""Activates or deactivates ThingsBoard devices and syncs local disabled list."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple

from tb import TB, TBError
from tb_cli import (
    CSV_FILE,
    DISABLED_FILE,
    edge_credentials,
    load_disabled,
    load_devices,
    save_disabled,
    target_devices,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_device(api: TB, name: str) -> Tuple[str, str]:
    device = api.device(name)
    if not device:
        raise TBError(f"No se encontro el dispositivo {name} en ThingsBoard.")
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
        help=f"Ruta al CSV de provision (default {CSV_FILE}).",
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
        help="Muestra que sucederia sin aplicar cambios en ThingsBoard ni en el archivo local.",
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
    edge_base, edge_user, edge_pass = edge_credentials()

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
        print(f"[DRY] El archivo local quedaria con {len(future_disabled)} desactivados.")
        return

    updated = 0
    missing = 0
    with TB(edge_base, edge_user, edge_pass) as api:
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
