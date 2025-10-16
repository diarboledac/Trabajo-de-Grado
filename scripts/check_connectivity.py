#!/usr/bin/env python3
"""Pequeña comprobación de conectividad contra ThingsBoard."""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv


def main() -> int:
    load_dotenv(override=True)
    base = os.getenv("TB_URL", "").rstrip("/")
    user = os.getenv("TB_USERNAME")
    password = os.getenv("TB_PASSWORD")
    if not base or not user or not password:
        print("[ERR] Faltan TB_URL, TB_USERNAME o TB_PASSWORD en el entorno", file=sys.stderr)
        return 2

    auth_url = f"{base}/api/auth/login"
    devices_url = f"{base}/api/tenant/devices?pageSize=1&page=0"
    payload = {"username": user, "password": password}

    with requests.Session() as session:
        resp = session.post(auth_url, json=payload, timeout=10)
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            print("[ERR] La respuesta de login no contiene token", file=sys.stderr)
            return 3
        print("[OK] Login exitoso")
        resp = session.get(devices_url, timeout=10, headers={"X-Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        print(f"[OK] Consulta de dispositivos (status {resp.status_code})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
