#!/usr/bin/env python3
"""Provision ThingsBoard devices and export their tokens."""
from __future__ import annotations

import csv
import json
import sys
import time

from simcore import (
    DATA_DIR,
    MAX_DEVICES,
    TOKENS_FILE,
    SimulatorConfig,
    ensure_data_dir,
    write_idle_metrics,
)
from tb import TB, TBError

CSV_FILE = DATA_DIR / "devices.csv"


def fail(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(code)


def main() -> None:
    config = SimulatorConfig.load()
    if not config.tb_url or not config.tb_username or not config.tb_password:
        fail("Config .env incompleta (TB_URL, TB_USERNAME, TB_PASSWORD)")

    ensure_data_dir()
    tokens_map: dict[str, str] = {}
    rows: list[list[str]] = []
    started = time.time()

    try:
        with TB(config.tb_url, config.tb_username, config.tb_password) as api:
            api.login()
            profile_id = config.device_profile_id or api.default_profile()
            if profile_id:
                msg = "fijado" if config.device_profile_id else "por defecto"
                print(f"[INFO] Device Profile {msg}: {profile_id}")
            else:
                print("[INFO] No se encontro Device Profile por defecto")

            for idx in range(1, config.device_count + 1):
                name = f"{config.device_prefix}-{idx:03d}"
                print(f"[INFO] Creando/recuperando '{name}'...")
                device = api.save_device(
                    name,
                    label=config.device_label,
                    dev_type=config.device_type,
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
                        "group": config.device_prefix,
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
    print(f"[OK] {len(rows)} dispositivos listos (maximo {MAX_DEVICES}). Tokens en: {TOKENS_FILE}")
    print(f"[OK] CSV exportado en: {CSV_FILE}. Tiempo total: {elapsed:.1f}s")
    write_idle_metrics("Dispositivos provisionados. Ejecuta run_telemetry.py para iniciar el trafico.")


if __name__ == "__main__":
    main()
