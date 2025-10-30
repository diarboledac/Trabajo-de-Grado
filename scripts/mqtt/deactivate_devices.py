#!/usr/bin/env python3
"""Desactiva dispositivos ThingsBoard y actualiza la lista local de deshabilitados."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from toggle_devices import execute_toggle, CSV_FILE, DISABLED_FILE


load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Desactiva dispositivos simulados para evitar que queden activos en ThingsBoard."
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--devices",
        nargs="+",
        help="Nombres exactos de los dispositivos a desactivar.",
    )
    target_group.add_argument(
        "--prefix",
        help="Desactiva todos los dispositivos cuyo nombre empiece por el prefijo indicado.",
    )
    target_group.add_argument(
        "--all",
        action="store_true",
        help="Desactiva todos los dispositivos registrados en el CSV.",
    )
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
        help=f"Archivo JSON de control (default {DISABLED_FILE}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra la acción a realizar sin aplicar cambios.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    execute_toggle(
        enable=False,
        devices=args.devices,
        prefix=args.prefix,
        include_all=args.all,
        csv_path=args.csv,
        disabled_file=args.disabled_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
