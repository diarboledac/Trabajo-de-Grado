#!/usr/bin/env python3
"""Ejecuta el flujo completo de prueba de carga MQTT."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List

from dotenv import load_dotenv


load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts" / "mqtt"


def run_python(script: Path, extra_args: List[str] | None = None) -> None:
    cmd = [sys.executable, str(script)]
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd, check=True)


def parse_args() -> tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Orquesta la provisi├│n, activaci├│n y ejecuci├│n del simulador MQTT. "
            "Cualquier argumento adicional ser├í reenviado a mqtt_stress_async.py."
        ),
        add_help=True,
    )
    parser.add_argument(
        "--skip-provision",
        action="store_true",
        help="Omite la llamada a create_devices.py (usa tokens existentes).",
    )
    parser.add_argument(
        "--skip-activate",
        action="store_true",
        help="No ejecuta activate_devices.py antes de la prueba.",
    )
    parser.add_argument(
        "--deactivate-after",
        action="store_true",
        help="Desactiva todos los dispositivos tras finalizar la prueba.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Sobrescribe el tiempo de ejecuci├│n en segundos para mqtt_stress_async.py.",
    )
    parser.add_argument(
        "--device-count",
        type=int,
        help="Sobrescribe la cantidad de dispositivos a usar en la prueba.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        help="Sobrescribe el intervalo de publicaci├│n (segundos).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Directorio personalizado para los eventos JSON del simulador.",
    )
    parser.add_argument(
        "--metrics-dir",
        type=Path,
        help="Directorio personalizado para las m├®tricas CSV del simulador.",
    )
    parser.add_argument(
        "--ramp-percentages",
        nargs="+",
        help="Secuencia de porcentajes a reenviar como --ramp-percentages.",
    )
    parser.add_argument(
        "--ramp-wait",
        type=float,
        help="Tiempo de espera entre rampas (segundos).",
    )
    parser.add_argument(
        "--qos",
        type=int,
        choices=[0, 1, 2],
        help="QoS MQTT (0, 1 o 2).",
    )
    parser.add_argument(
        "--disable-dashboard",
        action="store_true",
        help="Ejecuta mqtt_stress_async.py con --disable-dashboard.",
    )

    args, passthrough = parser.parse_known_args()
    return args, passthrough


def main() -> None:
    args, passthrough = parse_args()

    if not args.skip_provision:
        run_python(SCRIPTS_DIR / "create_devices.py")

    if not args.skip_activate:
        run_python(SCRIPTS_DIR / "activate_devices.py", ["--all"])

    stress_cmd: List[str] = []
    if args.duration is not None:
        stress_cmd.extend(["--duration", str(args.duration)])
    if args.device_count is not None:
        stress_cmd.extend(["--device-count", str(args.device_count)])
    if args.interval is not None:
        stress_cmd.extend(["--interval", str(args.interval)])
    if args.log_dir is not None:
        stress_cmd.extend(["--log-dir", str(args.log_dir)])
    if args.metrics_dir is not None:
        stress_cmd.extend(["--metrics-dir", str(args.metrics_dir)])
    if args.ramp_percentages:
        stress_cmd.append("--ramp-percentages")
        stress_cmd.extend(args.ramp_percentages)
    if args.ramp_wait is not None:
        stress_cmd.extend(["--ramp-wait", str(args.ramp_wait)])
    if args.qos is not None:
        stress_cmd.extend(["--qos", str(args.qos)])
    if args.disable_dashboard:
        stress_cmd.append("--disable-dashboard")

    stress_cmd.extend(passthrough)
    run_python(SCRIPTS_DIR / "mqtt_stress_async.py", stress_cmd)

    if args.deactivate_after:
        run_python(SCRIPTS_DIR / "deactivate_devices.py", ["--all"])


if __name__ == "__main__":
    main()
