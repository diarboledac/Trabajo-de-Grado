#!/usr/bin/env python3
"""Remove ThingsBoard devices that exceed the configured limit for a given prefix."""
from __future__ import annotations

import os
import re
import sys
from typing import Iterable

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv(override=True)

PREFIX = os.getenv("DEVICE_PREFIX", "sim")
KEEP_LIMIT = int(os.getenv("KEEP_DEVICE_COUNT", os.getenv("DEVICE_COUNT", "500")))


def ensure_credentials() -> tuple[str, str, str]:
    url = os.getenv("TB_URL", "").rstrip("/")
    user = os.getenv("TB_USERNAME")
    password = os.getenv("TB_PASSWORD")
    if not url or not user or not password:
        print("[ERR] Faltan TB_URL/TB_USERNAME/TB_PASSWORD en el entorno", file=sys.stderr)
        raise SystemExit(1)
    return url, user, password


def fetch_devices(api: TB, prefix: str) -> list[dict]:
    collected: list[dict] = []
    page = 0
    page_size = 500
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    while True:
        params = {"pageSize": page_size, "page": page, "textSearch": prefix}
        resp = api.session.get(f"{api.base}/api/tenant/devices", params=params, timeout=api.timeout)
        if resp.status_code != 200:
            raise TBError(f"Fallo al listar devices: {resp.status_code} {resp.text}")
        payload = resp.json()
        data = payload.get("data", [])
        collected.extend(d for d in data if d.get("name", "").startswith(prefix))
        if not payload.get("hasNext"):
            break
        page += 1
    collected.sort(key=lambda item: extract_index(item.get("name", ""), pattern))
    return collected


def extract_index(name: str, pattern: re.Pattern[str]) -> tuple[int, str]:
    match = pattern.match(name)
    if match:
        return int(match.group(1)), name
    return (10_000_000, name)


def delete_devices(api: TB, devices: Iterable[dict]) -> int:
    deleted = 0
    for dev in devices:
        dev_id = dev["id"]["id"]
        name = dev.get("name", dev_id)
        try:
            api.delete_device(dev_id)
            print(f"[DEL] {name}")
            deleted += 1
        except TBError as exc:
            print(f"[WARN] No se pudo borrar {name}: {exc}")
    return deleted


def main() -> None:
    url, user, password = ensure_credentials()
    limit = max(0, KEEP_LIMIT)

    with TB(url, user, password) as api:
        api.login()
        devices = fetch_devices(api, PREFIX)
        total = len(devices)
        if total <= limit:
            print(f"[INFO] {total} dispositivos con prefijo '{PREFIX}' (limite {limit}). No se elimina nada.")
            return

        survivors = devices[:limit]
        candidates = devices[limit:]
        print(
            f"[INFO] Detectados {total} dispositivos con prefijo '{PREFIX}'. "
            f"Se conservaran {len(survivors)} y se eliminaran {len(candidates)}."
        )
        deleted = delete_devices(api, candidates)
        print(f"[OK] Limpieza completada. Eliminados: {deleted}. Conservados: {len(survivors)}.")


if __name__ == "__main__":
    try:
        main()
    except TBError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        raise SystemExit(2)
