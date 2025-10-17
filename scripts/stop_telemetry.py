#!/usr/bin/env python3
"""Signal the telemetry simulator to stop gracefully."""
from __future__ import annotations

import time

from simcore import STOP_FILE, ensure_data_dir


def main() -> None:
    ensure_data_dir()
    STOP_FILE.write_text(str(time.time()), encoding="utf-8")
    print(f"[INFO] Señal de parada escrita en {STOP_FILE}.")
    print("[INFO] El proceso run_telemetry.py la detectará y detendrá el envío de datos.")


if __name__ == "__main__":
    main()
