#!/usr/bin/env python3
"""Create (or reuse) ThingsBoard devices and dump their tokens."""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from tb import TB, TBError

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PROVISION_DIR = DATA_DIR / "provisioning"
TOKENS_FILE = PROVISION_DIR / "tokens.json"
CSV_FILE = PROVISION_DIR / "devices.csv"

TB_URL = os.getenv("TB_URL", "").rstrip("/")
TB_USERNAME = os.getenv("TB_USERNAME")
TB_PASSWORD = os.getenv("TB_PASSWORD")
DEVICE_PREFIX = os.getenv("DEVICE_PREFIX", "sim")
DEVICE_COUNT = int(os.getenv("DEVICE_COUNT", "100"))
DEVICE_LABEL = os.getenv("DEVICE_LABEL", "sim-lab")
DEVICE_TYPE = os.getenv("DEVICE_TYPE", "sensor")
PROFILE_ID = os.getenv("DEVICE_PROFILE_ID")


def fail(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if not TB_URL or not TB_USERNAME or not TB_PASSWORD:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")

    ensure_dir(PROVISION_DIR)

    tokens_map: dict[str, str] = {}
    rows: list[list[str]] = []
    started = time.time()

    try:
        with TB(TB_URL, TB_USERNAME, TB_PASSWORD) as api:
            api.login()
            profile_id = PROFILE_ID or api.default_profile()
            if profile_id:
                msg = "fijado" if PROFILE_ID else "por defecto"
                print(f"[INFO] Device Profile {msg}: {profile_id}")
            else:
                print("[INFO] No se encontró Device Profile por defecto")

            for idx in range(1, DEVICE_COUNT + 1):
                name = f"{DEVICE_PREFIX}-{idx:03d}"
                print(f"[INFO] Creando/recuperando '{name}'...")
                device = api.save_device(
                    name,
                    label=DEVICE_LABEL,
                    dev_type=DEVICE_TYPE,
                    profile_id=profile_id,
                )
                dev_id = device["id"]["id"]
                token = api.token(dev_id)
                tokens_map[name] = token
                rows.append([dev_id, name, device.get("label", ""), token])
                api.set_attrs(
                    dev_id,
                    {
                        "batch": "sim-" + time.strftime("%Y%m%d"),
                        "group": DEVICE_PREFIX,
                        "index": idx,
                    },
                )

    except TBError as exc:
        fail(str(exc))

    TOKENS_FILE.write_text(json.dumps(tokens_map, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["device_id", "name", "label", "access_token"])
        writer.writerows(rows)

    elapsed = time.time() - started
    print(
        f"[OK] {len(rows)} dispositivos. Tokens en: {TOKENS_FILE}. CSV en: {CSV_FILE}. Tiempo: {elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
