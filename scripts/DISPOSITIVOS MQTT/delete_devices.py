#!/usr/bin/env python3
"""Delete ThingsBoard devices listed in data/devices.csv."""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]
CSV_FILE = ROOT / "data" / "provisioning" / "devices.csv"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")


def fail(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def delete_device(api: TB, dev_id: str) -> None:
    resp = api.session.delete(f"{api.base}/api/device/{dev_id}", timeout=api.timeout)
    if resp.status_code != 200:
        print(f"[WARN] No se pudo borrar {dev_id}: {resp.status_code} {resp.text}")
    else:
        print(f"[OK] Borrado {dev_id}")


def main() -> None:
    if not CSV_FILE.exists():
        fail(f"No existe {CSV_FILE}")
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")

    try:
        with TB(TB_URL, TB_USERNAME, TB_PASSWORD) as api:
            api.login()
            with CSV_FILE.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    delete_device(api, row["device_id"])
    except TBError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
