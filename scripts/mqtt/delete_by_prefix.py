#!/usr/bin/env python3
"""Delete ThingsBoard devices by prefix (edge and principal servers)."""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from typing import Dict, Iterable, List, Tuple

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv(override=True)

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")
TB_PARENT_URL = os.getenv("TB_PARENT_URL", "").rstrip("/")
TB_PARENT_USERNAME = os.getenv("TB_PARENT_USERNAME") or TB_USERNAME
TB_PARENT_PASSWORD = os.getenv("TB_PARENT_PASSWORD") or TB_PASSWORD
PREFIX = os.getenv("DEVICE_PREFIX", "sim")
PAGE_SIZE = 200


def fail(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def configured_clients() -> List[Tuple[str, TB]]:
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("[ERR] Faltan TB_URL/TB_USERNAME/TB_PASSWORD en el entorno")
    clients: List[Tuple[str, TB]] = [("edge", TB(TB_URL, TB_USERNAME, TB_PASSWORD))]
    if TB_PARENT_URL and TB_PARENT_URL != TB_URL:
        if not TB_PARENT_USERNAME or not TB_PARENT_PASSWORD:
            fail("[ERR] Config .env incompleta para el servidor principal (TB_PARENT_USERNAME/TB_PARENT_PASSWORD)")
        clients.append(("principal", TB(TB_PARENT_URL, TB_PARENT_USERNAME, TB_PARENT_PASSWORD)))
    return clients


def list_devices(api: TB) -> Iterable[Dict[str, object]]:
    page = 0
    while True:
        params = {"pageSize": PAGE_SIZE, "page": page}
        resp = api.session.get(f"{api.base}/api/tenant/devices", params=params, timeout=api.timeout)
        if resp.status_code != 200:
            raise TBError(f"Listar dispositivos fallo: {resp.status_code} {resp.text}")
        payload = resp.json()
        data = payload.get("data", [])
        for device in data:
            yield device
        if not payload.get("hasNext"):
            break
        page += 1


def delete_device(api: TB, device_id: str, name: str, scope: str) -> None:
    resp = api.session.delete(f"{api.base}/api/device/{device_id}", timeout=api.timeout)
    if resp.status_code == 200:
        print(f"[{scope}] [DEL] {name}")
    elif resp.status_code == 404:
        print(f"[{scope}] [WARN] {name} ya no existe")
    else:
        print(f"[{scope}] [WARN] No se pudo borrar {name}: {resp.status_code} {resp.text}")


def main() -> None:
    try:
        with ExitStack() as stack:
            clients = [(scope, stack.enter_context(client)) for scope, client in configured_clients()]
            for scope, api in clients:
                print(f"[INFO] Autenticando en {scope} ({api.base})...")
                api.login()

            for scope, api in clients:
                matches: Dict[str, Dict[str, object]] = {}
                for device in list_devices(api):
                    name = str(device.get("name", ""))
                    if name.startswith(PREFIX):
                        matches[str(device["id"]["id"])] = device
                print(f"[{scope}] [INFO] Encontrados {len(matches)} dispositivos con prefijo '{PREFIX}'")
                for device_id, device in matches.items():
                    name = str(device.get("name", device_id))
                    delete_device(api, device_id, name, scope)
    except TBError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
