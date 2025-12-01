#!/usr/bin/env python3
"""Valida resultados de pruebas y genera un reporte de advertencias."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "resultados"
REPORT_PATH = ROOT / "reportes" / "reporte_pruebas.txt"


def parse_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cargar_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def validar_prueba(path: Path) -> List[str]:
    advertencias: List[str] = []
    rows = cargar_csv(path)
    if not rows:
        return ["Archivo vacío o sin datos."]

    last = rows[-1]
    success = parse_float(last.get("successful_publishes"))
    failed = parse_float(last.get("failed_publishes"))
    elapsed = parse_float(last.get("elapsed_seconds"))
    total_devices = parse_float(last.get("total_devices"))
    connected = parse_float(last.get("connected_devices"))

    if success is None or elapsed is None:
        advertencias.append("Faltan campos básicos (successful_publishes o elapsed_seconds).")
    if success is not None and failed is not None and success + failed > 0:
        loss = failed / (success + failed) * 100.0
        if loss > 5:
            advertencias.append(f"Pérdida alta ({loss:.2f}%).")
    if total_devices is not None and connected is not None and connected < total_devices:
        advertencias.append("No todos los dispositivos permanecieron conectados.")

    latency = [parse_float(r.get("avg_latency_ms")) for r in rows if parse_float(r.get("avg_latency_ms")) is not None]
    if latency and max(latency) > 2000:
        advertencias.append("Latencia superior a 2 s detectada.")

    return advertencias


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(RESULTS_DIR.glob("*.csv"))
    if not csv_files:
        REPORT_PATH.write_text("No se encontraron CSV en resultados/.\n", encoding="utf-8")
        print("[INFO] No hay archivos que validar; se escribió un reporte básico.")
        return

    lines: List[str] = []
    for csv_path in csv_files:
        avisos = validar_prueba(csv_path)
        lines.append(f"Prueba: {csv_path.name}")
        if not avisos:
            lines.append("  - OK: sin advertencias.")
        else:
            for adv in avisos:
                lines.append(f"  - WARN: {adv}")
        lines.append("")  # separador

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Reporte generado en {REPORT_PATH}")


if __name__ == "__main__":
    main()
