#!/usr/bin/env python3
"""Delete ThingsBoard devices by prefix."""
from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

TB_URL = os.getenv('TB_URL', '').rstrip('/')
TB_USERNAME = os.getenv('TB_USERNAME')
TB_PASSWORD = os.getenv('TB_PASSWORD')
PREFIX = os.getenv('DEVICE_PREFIX', 'sim')

if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
    print('[ERR] Faltan TB_URL/TB_USERNAME/TB_PASSWORD en el entorno', file=sys.stderr)
    raise SystemExit(1)

login_url = f"{TB_URL}/api/auth/login"
resp = requests.post(login_url, json={'username': TB_USERNAME, 'password': TB_PASSWORD}, timeout=15)
resp.raise_for_status()
token = resp.json().get('token')
if not token:
    print('[ERR] No se recibió token JWT', file=sys.stderr)
    raise SystemExit(2)

session = requests.Session()
session.headers.update({'X-Authorization': f'Bearer {token}'})

page = 0
page_size = 200
to_delete = []
while True:
    params = {'pageSize': page_size, 'page': page}
    resp = session.get(f"{TB_URL}/api/tenant/devices", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get('data', [])
    to_delete.extend([d for d in data if d.get('name', '').startswith(PREFIX)])
    if not payload.get('hasNext'):
        break
    page += 1

print(f"[INFO] Encontrados {len(to_delete)} dispositivos con prefijo '{PREFIX}'")
for dev in to_delete:
    dev_id = dev['id']['id']
    name = dev.get('name', dev_id)
    resp = session.delete(f"{TB_URL}/api/device/{dev_id}", timeout=15)
    if resp.status_code == 200:
        print(f"[DEL] {name}")
    elif resp.status_code == 404:
        print(f"[WARN] {name} ya no existe")
    else:
        print(f"[WARN] No se pudo borrar {name}: {resp.status_code} {resp.text}")

print('[OK] Limpieza completada')
