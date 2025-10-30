#!/usr/bin/env python3
"""Delete ThingsBoard devices listed in data/devices.csv."""
from __future__ import annotations

import csv
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]
CSV_FILE = ROOT / "data" / "provisioning" / "devices.csv"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")
TB_PARENT_URL = os.getenv("TB_PARENT_URL", "").rstrip("/")
TB_PARENT_USERNAME = os.getenv("TB_PARENT_USERNAME") or TB_USERNAME
TB_PARENT_PASSWORD = os.getenv("TB_PARENT_PASSWORD") or TB_PASSWORD


def fail(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def configured_clients() -> List[Tuple[str, TB]]:
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")
    clients: List[Tuple[str, TB]] = [("edge", TB(TB_URL, TB_USERNAME, TB_PASSWORD))]
    if TB_PARENT_URL and TB_PARENT_URL != TB_URL:
        if not TB_PARENT_USERNAME or not TB_PARENT_PASSWORD:
            fail("Config .env incompleta para el servidor principal (TB_PARENT_USERNAME/TB_PARENT_PASSWORD)")
        clients.append(("principal", TB(TB_PARENT_URL, TB_PARENT_USERNAME, TB_PARENT_PASSWORD)))
    return clients


def delete_device(api: TB, dev_id: str, scope: str) -> None:
    resp = api.session.delete(f"{api.base}/api/device/{dev_id}", timeout=api.timeout)
    if resp.status_code == 200:
        print(f"[{scope}] [OK] Borrado {dev_id}")
    elif resp.status_code == 404:
        print(f"[{scope}] [WARN] {dev_id} ya no existe")
    else:
        print(f"[{scope}] [WARN] No se pudo borrar {dev_id}: {resp.status_code} {resp.text}")


def main() -> None:
    if not CSV_FILE.exists():
        fail(f"No existe {CSV_FILE}")

    try:
        with ExitStack() as stack:
            clients = [(scope, stack.enter_context(client)) for scope, client in configured_clients()]
            for scope, api in clients:
                print(f"[INFO] Autenticando en {scope} ({api.base})...")
                api.login()
            with CSV_FILE.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    dev_id = row["device_id"]
                    for scope, api in clients:
                        delete_device(api, dev_id, scope)
    except TBError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
