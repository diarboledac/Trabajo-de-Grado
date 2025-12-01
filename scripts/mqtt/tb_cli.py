#!/usr/bin/env python3
"""Shared helpers for ThingsBoard CLI scripts."""
from __future__ import annotations

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
PROVISION_DIR = ROOT / "data" / "provisioning"
CONTROL_DIR = ROOT / "data" / "control"
CSV_FILE = PROVISION_DIR / "devices.csv"
DISABLED_FILE = CONTROL_DIR / "disabled_devices.json"


def fail(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _edge_env() -> Tuple[str, str, str]:
    base = os.getenv("TB_URL", "").rstrip("/")
    user = os.getenv("TB_USERNAME")
    password = os.getenv("TB_PASSWORD")
    if not base or not user or not password:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")
    return base, user, password


def _parent_env(edge_base: str, edge_user: str, edge_pass: str) -> Tuple[str, str, str]:
    base = os.getenv("TB_PARENT_URL", "").rstrip("/")
    user = os.getenv("TB_PARENT_USERNAME") or edge_user
    password = os.getenv("TB_PARENT_PASSWORD") or edge_pass
    if base and not user:
        fail("Config .env incompleta para TB_PARENT_USERNAME")
    if base and not password:
        fail("Config .env incompleta para TB_PARENT_PASSWORD")
    return base, user, password


def configured_clients() -> List[Tuple[str, TB]]:
    """Return TB clients for edge and (optionally) parent servers."""
    edge_base, edge_user, edge_pass = _edge_env()
    parent_base, parent_user, parent_pass = _parent_env(edge_base, edge_user, edge_pass)

    clients: List[Tuple[str, TB]] = [("edge", TB(edge_base, edge_user, edge_pass))]
    if parent_base and parent_base != edge_base:
        clients.append(("principal", TB(parent_base, parent_user, parent_pass)))
    return clients


def edge_credentials() -> Tuple[str, str, str]:
    """Expose edge credentials with validation."""
    return _edge_env()


def load_devices(csv_path: Path = CSV_FILE) -> Dict[str, Dict[str, str]]:
    if not csv_path.exists():
        fail(f"No se encontro {csv_path}. Ejecuta primero create_devices.py.")
    devices: Dict[str, Dict[str, str]] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = row.get("name") or row.get("device_name")
            dev_id = row.get("device_id") or row.get("id")
            if not name or not dev_id:
                continue
            devices[name] = {"id": dev_id, "label": row.get("label", ""), "token": row.get("access_token", "")}
    if not devices:
        fail(f"{csv_path} no contiene dispositivos validos.")
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
                print(f"[WARN] {name} no esta en el CSV; se intentara buscarlo via API.", file=sys.stderr)
            targets.append(name)
        return targets
    if prefix:
        return [name for name in devices_map if name.startswith(prefix)]
    if include_all:
        return sorted(devices_map.keys())
    fail("Debes indicar --devices, --prefix o --all.")
    return []  # Unreachable, but keeps type checkers happy.


def load_disabled(path: Path = DISABLED_FILE) -> Set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError as exc:
        fail(f"Archivo invalido {path}: {exc}")
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
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Archivo generado por toggle_devices.py. Editar con cuidado.",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "TB",
    "TBError",
    "CSV_FILE",
    "DISABLED_FILE",
    "edge_credentials",
    "configured_clients",
    "load_devices",
    "target_devices",
    "load_disabled",
    "save_disabled",
    "fail",
]
