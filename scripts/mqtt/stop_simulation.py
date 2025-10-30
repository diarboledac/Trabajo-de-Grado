#!/usr/bin/env python3
"""Utility to stop a running mqtt_stress_async simulation gracefully."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CONTROL_DIR = DATA_DIR / "control"
DEFAULT_PID_FILE = CONTROL_DIR / "mqtt_stress.pid"
DEFAULT_WAIT_SECONDS = 10.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detiene el simulador mqtt_stress_async en ejecución.")
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=Path(os.getenv("SIM_PID_FILE") or DEFAULT_PID_FILE),
        help="Archivo con el PID del orquestador (por defecto data/control/mqtt_stress.pid).",
    )
    parser.add_argument(
        "--signal",
        choices=["term", "int", "kill"],
        default="term",
        help="Señal a enviar: term=SIGTERM, int=SIGINT, kill=SIGKILL (si está disponible).",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_WAIT_SECONDS,
        help=f"Segundos a esperar por el apagado limpio antes de forzar (default {DEFAULT_WAIT_SECONDS}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Si el proceso no termina en el tiempo de espera, usa SIGKILL (cuando esté disponible).",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Elimina el archivo PID al finalizar si el proceso ya no está activo.",
    )
    return parser.parse_args()


def resolve_signal(choice: str) -> signal.Signals:
    mapping = {"term": signal.SIGTERM, "int": signal.SIGINT}
    if choice == "kill":
        if hasattr(signal, "SIGKILL"):
            return signal.SIGKILL  # type: ignore[attr-defined]
        print("[WARN] SIGKILL no está disponible en esta plataforma; usando SIGTERM.", file=sys.stderr)
        return signal.SIGTERM
    return mapping[choice]


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(path: Path) -> int:
    if not path.exists():
        raise SystemExit(f"No se encontró el archivo PID: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"El archivo PID {path} está vacío.")
    try:
        return int(text)
    except ValueError as exc:
        raise SystemExit(f"Contenido inválido en {path}: {text!r}") from exc


def wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.25)
    return not process_alive(pid)


def main() -> None:
    args = parse_args()
    pid = read_pid(args.pid_file)
    if not process_alive(pid):
        print(f"[INFO] El proceso {pid} ya no está activo.")
        if args.cleanup and args.pid_file.exists():
            args.pid_file.unlink()
        return

    sig = resolve_signal(args.signal)
    print(f"[INFO] Enviando {sig.name} a PID {pid}...")
    try:
        os.kill(pid, sig)
    except PermissionError:
        raise SystemExit(f"No se pudo enviar {sig.name} al proceso {pid} (permiso denegado).")

    if wait_for_exit(pid, args.wait):
        print("[OK] Simulación detenida correctamente.")
        if args.cleanup and args.pid_file.exists():
            args.pid_file.unlink()
        return

    if args.force and hasattr(signal, "SIGKILL"):
        print("[WARN] Tiempo de espera agotado. Intentando SIGKILL...")
        try:
            os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined]
        except PermissionError:
            raise SystemExit(f"No se pudo enviar SIGKILL al proceso {pid} (permiso denegado).")
        if wait_for_exit(pid, 5.0):
            print("[OK] Simulación finalizada tras SIGKILL.")
            if args.cleanup and args.pid_file.exists():
                args.pid_file.unlink()
            return

    raise SystemExit("No se pudo detener la simulación. Verifica manualmente el proceso.")


if __name__ == "__main__":
    main()
