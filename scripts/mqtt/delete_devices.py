#!/usr/bin/env python3
"""Delete ThingsBoard devices listed in data/devices.csv."""
from __future__ import annotations

import csv
from contextlib import ExitStack

from tb import TB, TBError
from tb_cli import CSV_FILE, configured_clients, fail


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
